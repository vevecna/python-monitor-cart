"""
monitor_estoque.py — Monitora a disponibilidade de um produto e clica/notifica.

Requisitos:
    pip install playwright requests
    playwright install chromium

Uso:
    export TELEGRAM_TOKEN="123:ABC"      # opcional
    export TELEGRAM_CHAT_ID="987654"     # opcional
    python monitor_estoque.py "https://www.loja.com.br/produto/123" --clicar

Primeira execução: rode com --visivel, faça login na loja manualmente e feche.
O perfil em ./perfil_navegador guarda a sessão, então o item vai para o
carrinho da SUA conta (e você finaliza no seu navegador/app normalmente).
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
            r.raise_for_status()
        except requests.RequestException as e:
            # Loga só o tipo do erro: a mensagem da exceção contém a URL COM o token.
            log.error("Falha ao enviar Telegram: %s", type(e).__name__)


# ─────────────────────── Verificação da página ───────────────────────
class VerificadorEstoque:
    def __init__(self, page: Page, cfg: Config) -> None:
        self._page = page
        self._cfg = cfg

    def verificar(self) -> Estado:
        try:
            resp = self._page.goto(self._cfg.url, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            log.warning("Timeout ao carregar a página.")
            return Estado.DESCONHECIDO

        if resp is None or not resp.ok:
            log.warning("HTTP inesperado: %s", resp.status if resp else "sem resposta")
            return Estado.DESCONHECIDO

        frase = self._page.get_by_text(self._cfg.frase_indisponivel)
        botao = self._page.locator(self._cfg.seletor_botao)

        # Sites modernos renderizam via JS: espera a frase OU o botão aparecer.
        try:
            frase.or_(botao).first.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            log.warning("Nem a frase nem o botão apareceram (layout mudou? CAPTCHA?).")
            return Estado.DESCONHECIDO

        if frase.count() > 0:
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
    return notificadores


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor de estoque")
    parser.add_argument("url")
    parser.add_argument("--clicar", action="store_true", help="adiciona ao carrinho automaticamente")
    parser.add_argument("--visivel", action="store_true", help="abre o navegador com janela")
    parser.add_argument("--intervalo", type=int, default=120)
    args = parser.parse_args()

    cfg = Config(url=args.url, clicar_automaticamente=args.clicar,
                 headless=not args.visivel, intervalo_seg=args.intervalo)

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
