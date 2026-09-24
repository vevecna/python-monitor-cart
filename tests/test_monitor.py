"""
Testes do classificador de estoque e do notificador do Telegram.

Todos usam HTML local via `page.set_content(...)`; nenhum toca a loja real.

    pip install -r requirements-dev.txt
    pytest
"""
from __future__ import annotations

import logging
import os

import pytest
import requests
from playwright.sync_api import sync_playwright

from monitor.config import Config, carregar_env
from monitor.notificadores import NotificadorTelegram
from monitor.verificador import Estado, VerificadorEstoque

FRASE = "Este produto não está disponível no momento"


# ─────────────────────────────── Fixtures ───────────────────────────────
@pytest.fixture(scope="session")
def navegador():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(navegador):
    pagina = navegador.new_page()
    yield pagina
    pagina.close()


def montar_verificador(page) -> VerificadorEstoque:
    """Verificador com timeout curto: a página já está em memória, não há JS para esperar."""
    cfg = Config(url="http://exemplo.invalido/produto", timeout_conteudo_ms=1_000)
    return VerificadorEstoque(page, cfg)


# ──────────────────────── Classificação do estado ────────────────────────
def test_frase_visivel_resulta_em_indisponivel(page):
    page.set_content(f"<p>{FRASE}</p>")
    assert montar_verificador(page).classificar() is Estado.INDISPONIVEL


def test_botao_habilitado_sem_frase_resulta_em_disponivel(page):
    page.set_content("<button>Adicionar ao carrinho</button>")
    assert montar_verificador(page).classificar() is Estado.DISPONIVEL


def test_botao_desabilitado_resulta_em_desconhecido(page):
    """Botão presente mas travado não prova disponibilidade (invariante 1)."""
    page.set_content("<button disabled>Adicionar ao carrinho</button>")
    assert montar_verificador(page).classificar() is Estado.DESCONHECIDO


def test_pagina_vazia_resulta_em_desconhecido(page):
    """Nem frase nem botão: layout mudou, CAPTCHA ou bloqueio — nunca DISPONIVEL."""
    page.set_content("<html><body></body></html>")
    assert montar_verificador(page).classificar() is Estado.DESCONHECIDO


def test_frase_oculta_no_dom_nao_marca_indisponivel(page):
    """SPAs mantêm o aviso oculto e alternam o CSS; contar nós daria falso INDISPONIVEL."""
    page.set_content(
        f'<p style="display:none">{FRASE}</p>'
        "<button>Adicionar ao carrinho</button>"
    )
    assert montar_verificador(page).classificar() is Estado.DISPONIVEL


# ────────────────────────────── Configuração ──────────────────────────────
def test_intervalo_abaixo_do_minimo_e_rejeitado():
    with pytest.raises(ValueError, match="mínimo"):
        Config(url="http://exemplo.invalido", intervalo_seg=5)


def test_intervalo_no_piso_e_aceito():
    assert Config(url="http://exemplo.invalido", intervalo_seg=60).intervalo_seg == 60


# ────────────────────────── Leitura do arquivo .env ──────────────────────────
@pytest.fixture
def ambiente(monkeypatch) -> dict[str, str]:
    """Substitui os.environ por um dicionário vazio, isolando o teste do ambiente real."""
    falso: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", falso)
    return falso


def escrever_env(tmp_path, conteudo: str) -> str:
    arquivo = tmp_path / ".env"
    arquivo.write_text(conteudo, encoding="utf-8")
    arquivo.chmod(0o600)
    return str(arquivo)


def test_env_carrega_pares_simples(tmp_path, ambiente):
    carregar_env(escrever_env(tmp_path, "TELEGRAM_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=987\n"))
    assert ambiente["TELEGRAM_TOKEN"] == "123:ABC"
    assert ambiente["TELEGRAM_CHAT_ID"] == "987"


def test_env_remove_aspas_e_prefixo_export(tmp_path, ambiente):
    carregar_env(escrever_env(tmp_path, "export TELEGRAM_TOKEN=\"123:ABC\"\nTELEGRAM_CHAT_ID='987'\n"))
    assert ambiente["TELEGRAM_TOKEN"] == "123:ABC"
    assert ambiente["TELEGRAM_CHAT_ID"] == "987"


def test_env_ignora_comentarios_e_linhas_vazias(tmp_path, ambiente):
    carregar_env(escrever_env(tmp_path, "# comentário\n\n  \nTELEGRAM_CHAT_ID=987\n"))
    assert ambiente["TELEGRAM_CHAT_ID"] == "987"
    assert "#" not in "".join(ambiente)


def test_ambiente_existente_tem_precedencia(tmp_path, ambiente):
    """Permite sobrescrever pontualmente: TELEGRAM_CHAT_ID=outro python monitor_estoque.py ..."""
    ambiente["TELEGRAM_CHAT_ID"] = "do-ambiente"
    carregar_env(escrever_env(tmp_path, "TELEGRAM_CHAT_ID=do-arquivo\n"))
    assert ambiente["TELEGRAM_CHAT_ID"] == "do-ambiente"


def test_env_preserva_cerquilha_no_valor(tmp_path, ambiente):
    """Um token pode conter `#`; truncar nele silenciosamente seria pior que ignorar a convenção."""
    carregar_env(escrever_env(tmp_path, "TELEGRAM_TOKEN=123:AB#CD\n"))
    assert ambiente["TELEGRAM_TOKEN"] == "123:AB#CD"


def test_env_ausente_nao_quebra(tmp_path, ambiente):
    carregar_env(str(tmp_path / "nao-existe"))
    assert "TELEGRAM_TOKEN" not in ambiente


def test_env_avisa_sobre_linha_malformada(tmp_path, ambiente, caplog):
    with caplog.at_level(logging.WARNING):
        carregar_env(escrever_env(tmp_path, "sem_igual\nTELEGRAM_CHAT_ID=987\n"))
    assert "linha 1" in caplog.text
    assert ambiente["TELEGRAM_CHAT_ID"] == "987"   # a linha boa ainda é lida


def test_env_nao_loga_valores(tmp_path, ambiente, caplog):
    with caplog.at_level(logging.DEBUG):
        carregar_env(escrever_env(tmp_path, "TELEGRAM_TOKEN=123:TOKEN_SECRETO\n"))
    assert "TOKEN_SECRETO" not in caplog.text
    assert "TELEGRAM_TOKEN" in caplog.text   # o nome da chave, sim


def test_env_avisa_sobre_permissao_aberta(tmp_path, ambiente, caplog):
    caminho = escrever_env(tmp_path, "TELEGRAM_TOKEN=123:ABC\n")
    os.chmod(caminho, 0o644)
    with caplog.at_level(logging.WARNING):
        carregar_env(caminho)
    assert "chmod 600" in caplog.text


# ─────────────────────────── Notificador Telegram ───────────────────────────
class RespostaFalsa:
    """Dublê mínimo de `requests.Response` para os testes do notificador."""

    def __init__(self, ok: bool = True, status_code: int = 200, corpo: dict | None = None) -> None:
        self.ok = ok
        self.status_code = status_code
        self._corpo = corpo or {}

    def json(self) -> dict:
        return self._corpo


def test_telegram_envia_para_o_chat_correto(monkeypatch):
    capturado: dict[str, object] = {}

    def post_falso(url, data, timeout):
        capturado.update(url=url, data=data, timeout=timeout)
        return RespostaFalsa()

    monkeypatch.setattr(requests, "post", post_falso)
    NotificadorTelegram("123:ABC", "987").enviar("produto disponível")

    assert capturado["data"] == {"chat_id": "987", "text": "produto disponível"}
    assert capturado["timeout"] == 10


def test_telegram_loga_motivo_da_recusa(monkeypatch, caplog):
    """Sem a descrição do corpo, um chat_id errado viraria um log sem pista nenhuma."""
    token = "123:TOKEN_SECRETO"
    resposta = RespostaFalsa(ok=False, status_code=400, corpo={"description": "chat not found"})
    monkeypatch.setattr(requests, "post", lambda url, data, timeout: resposta)

    with caplog.at_level(logging.ERROR):
        NotificadorTelegram(token, "chat-errado").enviar("oi")

    assert "chat not found" in caplog.text
    assert "400" in caplog.text
    assert "TOKEN_SECRETO" not in caplog.text


def test_telegram_nao_vaza_token_no_log(monkeypatch, caplog):
    """A mensagem da exceção do requests carrega a URL COM o token."""
    token = "123:TOKEN_SECRETO"

    def post_falso(url, data, timeout):
        raise requests.RequestException(
            f"falha ao conectar em https://api.telegram.org/bot{token}/sendMessage"
        )

    monkeypatch.setattr(requests, "post", post_falso)
    with caplog.at_level(logging.ERROR):
        NotificadorTelegram(token, "987").enviar("oi")   # não deve propagar a exceção

    assert "TOKEN_SECRETO" not in caplog.text
    assert "RequestException" in caplog.text
