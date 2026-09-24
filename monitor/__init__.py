"""
Monitor de estoque: acompanha a página de um produto e avisa quando ele fica disponível.

Ponto de entrada: `python -m monitor`.
"""
from __future__ import annotations

from monitor.config import INTERVALO_MINIMO_SEG, Config, carregar_env
from monitor.monitor import Monitor
from monitor.notificadores import (
    Notificador,
    NotificadorConsole,
    NotificadorTelegram,
    montar_notificadores,
)
from monitor.verificador import Estado, VerificadorEstoque

__all__ = [
    "INTERVALO_MINIMO_SEG",
    "Config",
    "Estado",
    "Monitor",
    "Notificador",
    "NotificadorConsole",
    "NotificadorTelegram",
    "VerificadorEstoque",
    "carregar_env",
    "montar_notificadores",
]
