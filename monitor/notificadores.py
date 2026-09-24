"""
Canais de notificação.

Um canal novo é uma subclasse de `Notificador` registrada em `montar_notificadores()`;
o `Monitor` não precisa saber que ela existe.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import requests

log = logging.getLogger(__name__)


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
