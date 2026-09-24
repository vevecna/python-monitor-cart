"""
Interface de linha de comando.

    python -m monitor "https://www.loja.com.br/produto/123" --login
    python -m monitor "https://www.loja.com.br/produto/123" --clicar --intervalo 300
    python -m monitor --testar-notificacao

Primeiro use --login para autenticar: o perfil em ./perfil_navegador guarda a sessão,
então o item vai para o carrinho da SUA conta e você finaliza a compra normalmente.

Cada loja usa um botão e uma frase diferentes: descubra-os com F12 e passe via
--seletor e --frase. Valide com --visivel antes de deixar rodando.
"""
from __future__ import annotations

import argparse
import logging

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from monitor.config import INTERVALO_MINIMO_SEG, Config, carregar_env
from monitor.monitor import Monitor
from monitor.notificadores import montar_notificadores
from monitor.verificador import VerificadorEstoque

log = logging.getLogger("monitor")


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
        prog="python -m monitor",
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
        log.error("Só o console está ativo — defina TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no .env.")
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


def executar_monitoramento(cfg: Config) -> None:
    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(cfg.perfil_navegador, headless=cfg.headless)
        page = contexto.pages[0] if contexto.pages else contexto.new_page()
        try:
            Monitor(VerificadorEstoque(page, cfg), montar_notificadores(), cfg).executar()
        except KeyboardInterrupt:
            log.info("Encerrado pelo usuário.")
        finally:
            contexto.close()


def main() -> None:
    # A configuração do logging vive aqui, e não nos módulos: importar o pacote
    # como biblioteca não deve mexer no logging de quem importou.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    carregar_env()

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
    else:
        executar_monitoramento(cfg)


if __name__ == "__main__":
    main()
