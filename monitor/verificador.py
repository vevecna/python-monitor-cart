"""Carrega a página do produto e classifica a disponibilidade."""
from __future__ import annotations

import logging
from enum import Enum, auto

from playwright.sync_api import Page, TimeoutError as PWTimeout

from monitor.config import Config

log = logging.getLogger(__name__)


# ─────────────── Estados possíveis (mini FSM de 3 estados) ───────────────
class Estado(Enum):
    DISPONIVEL = auto()
    INDISPONIVEL = auto()
    DESCONHECIDO = auto()   # erro, timeout, CAPTCHA, layout mudou...


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
