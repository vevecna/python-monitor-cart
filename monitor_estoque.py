"""
monitor_estoque.py — Monitora a disponibilidade de um produto e clica/notifica.

Requisitos:
    pip install playwright requests
    playwright install chromium

Uso:
    export TELEGRAM_TOKEN="123:ABC"      # opcional
    export TELEGRAM_CHAT_ID="987654"     # opcional

    # 1. Uma vez por loja: autentique-se e salve a sessão.
    python monitor_estoque.py "https://www.loja.com.br/produto/123" --login

    # 2. Monitoramento.
    python monitor_estoque.py "https://www.loja.com.br/produto/123" --clicar

O perfil em ./perfil_navegador guarda a sessão, então o item vai para o
carrinho da SUA conta (e você finaliza no seu navegador/app normalmente).

Cada loja usa um botão e uma frase diferentes: descubra-os com F12 e passe
via --seletor e --frase. Valide com --visivel antes de deixar rodando.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto

import requests
from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("monitor")


# Piso de cortesia com a loja: não existe motivo legítimo para verificar mais
# rápido que isso, e intervalos curtos parecem ataque de negação de serviço.
INTERVALO_MINIMO_SEG = 60


# ─────────────────────────── Configuração ───────────────────────────
@dataclass(frozen=True)
class Config:
    url: str
    frase_indisponivel: str = "Este produto não está disponível no momento"
    seletor_botao: str = "button:has-text('Adicionar ao carrinho')"  # ajuste via F12
    intervalo_seg: int = 120          # respeite o site: nada de loop a cada 1s
    jitter_seg: int = 30              # aleatoriedade para não bater sempre no mesmo segundo
    clicar_automaticamente: bool = False
    headless: bool = True
    perfil_navegador: str = "./perfil_navegador"
    timeout_conteudo_ms: int = 15_000   # espera pela frase ou pelo botão renderizado via JS

    def __post_init__(self) -> None:
        """Valida o piso de intervalo também para uso programático, não só pela CLI."""
        if self.intervalo_seg < INTERVALO_MINIMO_SEG:
            raise ValueError(
                f"intervalo_seg={self.intervalo_seg}s é menor que o mínimo de "
                f"{INTERVALO_MINIMO_SEG}s exigido por respeito ao site."
            )
        if self.jitter_seg < 0:
            raise ValueError("jitter_seg não pode ser negativo.")


# ─────────────── Estados possíveis (mini FSM de 3 estados) ───────────────
class Estado(Enum):
    DISPONIVEL = auto()
    INDISPONIVEL = auto()
    DESCONHECIDO = auto()   # erro, timeout, CAPTCHA, layout mudou...


# ─────────────────────────── Notificadores ───────────────────────────
class Notificador(ABC):
    @abstractmethod
    def enviar(self, mensagem: str) -> None: ...


class NotificadorConsole(Notificador):
    def enviar(self, mensagem: str) -> None:
        print(f"\a🔔 {mensagem}", flush=True)   # \a = beep do terminal


class NotificadorTelegram(Notificador):
    def __init__(self, token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id

    def enviar(self, mensagem: str) -> None:
        try:
            r = requests.post(self._url, data={"chat_id": self._chat_id, "text": mensagem}, timeout=10)
        except requests.RequestException as e:
            # Loga só o tipo do erro: a mensagem da exceção contém a URL COM o token.
            log.error("Falha ao enviar Telegram: %s", type(e).__name__)
            return

        if not r.ok:
            # O corpo da resposta diz o motivo ("chat not found", "Unauthorized") e,
            # ao contrário da URL, não contém o token — então é seguro logar.
            log.error("Telegram recusou (HTTP %s): %s", r.status_code, self._descricao(r))

    @staticmethod
    def _descricao(resposta: requests.Response) -> str:
        try:
            return str(resposta.json().get("description", "sem descrição"))
        except ValueError:
            return "resposta não era JSON"


# ─────────────────────── Verificação da página ───────────────────────
class VerificadorEstoque:
    def __init__(self, page: Page, cfg: Config) -> None:
        self._page = page
        self._cfg = cfg

    def verificar(self) -> Estado:
        """Navega até a URL e classifica o resultado."""
        try:
            resp = self._page.goto(self._cfg.url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            log.warning("Timeout ao carregar a página.")
            return Estado.DESCONHECIDO

        if resp is None or not resp.ok:
            log.warning("HTTP inesperado: %s", resp.status if resp else "sem resposta")
            return Estado.DESCONHECIDO

        return self.classificar()

    def classificar(self) -> Estado:
        """Classifica o DOM já carregado. Separado de `verificar` para permitir teste local."""
        frase = self._page.get_by_text(self._cfg.frase_indisponivel)
        botao = self._page.locator(self._cfg.seletor_botao)

        # Sites modernos renderizam via JS: espera a frase OU o botão aparecer.
        # O `filter(visible=True)` é essencial: sem ele, `.first` resolve pelo primeiro
        # elemento na ordem do DOM, que pode ser o aviso oculto, e a espera estoura
        # mesmo com o botão visível logo abaixo.
        try:
            (frase.or_(botao)
                  .filter(visible=True).first
                  .wait_for(state="visible", timeout=self._cfg.timeout_conteudo_ms))
        except PWTimeout:
            log.warning("Nem a frase nem o botão apareceram (layout mudou? CAPTCHA?).")
            return Estado.DESCONHECIDO

        # `is_visible` e não `count`: SPAs mantêm o aviso de indisponível no DOM
        # oculto e só alternam o CSS, então contar nós daria falso INDISPONIVEL.
        if frase.first.is_visible():
            return Estado.INDISPONIVEL

        # Confirmação POSITIVA: ausência da frase sozinha não prova disponibilidade.
        if botao.first.is_visible() and botao.first.is_enabled():
            return Estado.DISPONIVEL
        return Estado.DESCONHECIDO

    def clicar_carrinho(self) -> bool:
        try:
            self._page.locator(self._cfg.seletor_botao).first.click(timeout=10_000)
            self._page.wait_for_timeout(3_000)   # dá tempo da requisição do carrinho concluir
            return True
        except PWTimeout:
            log.error("Não consegui clicar no botão.")
            return False


# ───────────────────────── Loop de monitoramento ─────────────────────────
class Monitor:
    MAX_FALHAS_ALERTA = 5

    def __init__(self, verificador: VerificadorEstoque, notificadores: list[Notificador], cfg: Config) -> None:
        self._verificador = verificador
        self._notificadores = notificadores
        self._cfg = cfg

    def _notificar(self, mensagem: str) -> None:
        for n in self._notificadores:
            n.enviar(mensagem)

    def _calcular_espera(self, falhas: int) -> float:
        base = self._cfg.intervalo_seg * (2 ** min(falhas, 4))   # backoff exponencial, teto 16x
        return base + random.uniform(0, self._cfg.jitter_seg)

    def executar(self) -> None:
        falhas = 0
        while True:
            estado = self._verificador.verificar()
            log.info("Estado: %s", estado.name)

            if estado is Estado.DISPONIVEL:
                msg = f"✅ Produto disponível: {self._cfg.url}"
                if self._cfg.clicar_automaticamente and self._verificador.clicar_carrinho():
                    msg += "\n🛒 Adicionado ao carrinho — finalize a compra manualmente."
                self._notificar(msg)
                return  # notifica UMA vez e encerra (evita spam)

            falhas = falhas + 1 if estado is Estado.DESCONHECIDO else 0
            if falhas == self.MAX_FALHAS_ALERTA:
                self._notificar(f"⚠️ {falhas} verificações falharam seguidas. Revise seletores/URL.")

            espera = self._calcular_espera(falhas)
            log.info("Próxima verificação em %.0fs", espera)
            time.sleep(espera)


# ─────────────────────────────── main ───────────────────────────────
def montar_notificadores() -> list[Notificador]:
    notificadores: list[Notificador] = [NotificadorConsole()]
    token, chat_id = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        notificadores.append(NotificadorTelegram(token, chat_id))
        log.info("Telegram configurado.")
    elif token or chat_id:
        # Metade configurada é quase sempre um typo no nome da variável; ficar
        # em silêncio faria você descobrir só quando o produto aparecesse.
        faltando = "TELEGRAM_CHAT_ID" if token else "TELEGRAM_TOKEN"
        log.warning("Telegram desativado: falta a variável %s.", faltando)
    else:
        log.info("Telegram não configurado; notificação apenas no terminal.")
    return notificadores


def _intervalo(valor: str) -> int:
    """Rejeita intervalos abaixo do piso já no parse, com mensagem clara."""
    seg = int(valor)
    if seg < INTERVALO_MINIMO_SEG:
        raise argparse.ArgumentTypeError(
            f"intervalo mínimo é {INTERVALO_MINIMO_SEG}s (respeito ao site); recebido {seg}s"
        )
    return seg


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitora a disponibilidade de um produto e notifica (opcionalmente clica no carrinho).",
        epilog="Sempre coloque a URL entre aspas: ela costuma conter & e ?.",
    )
    parser.add_argument("url", nargs="?", help="URL da página do produto (entre aspas)")
    parser.add_argument("--clicar", action="store_true",
                        help="adiciona ao carrinho automaticamente ao detectar disponibilidade")
    parser.add_argument("--visivel", action="store_true", help="abre o navegador com janela")
    parser.add_argument("--intervalo", type=_intervalo, default=120,
                        metavar="SEG", help=f"segundos entre verificações (mínimo {INTERVALO_MINIMO_SEG}, padrão 120)")
    parser.add_argument("--seletor", default=Config.seletor_botao, metavar="CSS",
                        help="seletor do botão de carrinho; prefira id/data-testid (padrão: %(default)s)")
    parser.add_argument("--frase", default=Config.frase_indisponivel, metavar="TEXTO",
                        help="frase que a loja exibe quando o produto está esgotado (padrão: %(default)s)")
    parser.add_argument("--perfil", default=Config.perfil_navegador, metavar="DIR",
                        help="diretório do perfil do Chromium, onde fica a sessão (padrão: %(default)s)")
    parser.add_argument("--login", action="store_true",
                        help="abre a página com janela para você logar na loja e sai; a sessão fica salva no perfil")
    parser.add_argument("--testar-notificacao", action="store_true", dest="testar_notificacao",
                        help="envia uma mensagem de teste pelos canais configurados e sai (não abre o navegador)")
    return parser


def testar_notificacao() -> int:
    """Dispara uma mensagem de teste. Retorna o código de saída do processo."""
    notificadores = montar_notificadores()
    if len(notificadores) == 1:
        log.error("Só o console está ativo — exporte TELEGRAM_TOKEN e TELEGRAM_CHAT_ID.")
        return 1
    for n in notificadores:
        n.enviar("🧪 Teste do monitor de estoque. Se você recebeu isto, está configurado.")
    log.info("Teste disparado. Se nada chegou no Telegram, procure um ERROR nas linhas acima.")
    return 0


def executar_login(cfg: Config) -> None:
    """Abre o navegador visível e espera o usuário autenticar antes de encerrar."""
    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(cfg.perfil_navegador, headless=False)
        page = contexto.pages[0] if contexto.pages else contexto.new_page()
        try:
            page.goto(cfg.url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            log.warning("A página demorou a carregar, mas a janela continua aberta.")
        print(f"\nFaça login na loja na janela aberta. A sessão será salva em {cfg.perfil_navegador}/")
        input("Quando terminar, volte aqui e pressione Enter para fechar... ")
        contexto.close()
    log.info("Sessão salva. Agora rode sem --login para começar a monitorar.")


def main() -> None:
    parser = _montar_parser()
    args = parser.parse_args()

    if args.testar_notificacao:
        raise SystemExit(testar_notificacao())
    if not args.url:
        parser.error("a URL é obrigatória (só pode ser omitida com --testar-notificacao)")

    cfg = Config(url=args.url, clicar_automaticamente=args.clicar,
                 headless=not args.visivel, intervalo_seg=args.intervalo,
                 seletor_botao=args.seletor, frase_indisponivel=args.frase,
                 perfil_navegador=args.perfil)

    if args.login:
        executar_login(cfg)
        return

    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(cfg.perfil_navegador, headless=cfg.headless)
        page = contexto.pages[0] if contexto.pages else contexto.new_page()
        try:
            Monitor(VerificadorEstoque(page, cfg), montar_notificadores(), cfg).executar()
        except KeyboardInterrupt:
            log.info("Encerrado pelo usuário.")
        finally:
            contexto.close()


if __name__ == "__main__":
    main()