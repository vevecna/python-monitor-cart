"""Loop de monitoramento: verifica, espera com backoff e dispara as notificações."""
from __future__ import annotations

import logging
import random
import time

from monitor.config import Config
from monitor.notificadores import Notificador
from monitor.verificador import Estado, VerificadorEstoque

log = logging.getLogger(__name__)


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
