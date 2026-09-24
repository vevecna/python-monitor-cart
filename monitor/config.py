"""Parâmetros imutáveis da execução e leitura do arquivo `.env`."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Piso de cortesia com a loja: não existe motivo legítimo para verificar mais
# rápido que isso, e intervalos curtos parecem ataque de negação de serviço.
INTERVALO_MINIMO_SEG = 60

ARQUIVO_ENV = ".env"


# ──────────────────────── Configuração via arquivo ────────────────────────
def _sem_aspas(valor: str) -> str:
    """Remove um par de aspas que envolva todo o valor."""
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
        return valor[1:-1]
    return valor


def carregar_env(caminho: str = ARQUIVO_ENV) -> None:
    """
    Lê pares CHAVE=VALOR de um arquivo .env para o ambiente.

    Variáveis já presentes no ambiente têm precedência, para permitir sobrescrever
    pontualmente numa execução. Os valores nunca são logados — o arquivo guarda o
    token do Telegram. O `#` só inicia comentário no começo da linha, porque um
    token pode contê-lo e truncá-lo em silêncio seria pior que ignorar a convenção.
    """
    arquivo = Path(caminho)
    if not arquivo.is_file():
        return

    if arquivo.stat().st_mode & 0o077:
        log.warning("%s está legível por outros usuários; rode `chmod 600 %s`.", caminho, caminho)

    carregadas: list[str] = []
    for numero, bruta in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1):
        linha = bruta.strip()
        if not linha or linha.startswith("#"):
            continue
        if linha.startswith("export "):
            linha = linha[len("export "):].lstrip()

        chave, separador, valor = linha.partition("=")
        chave = chave.strip()
        if not separador or not chave:
            log.warning("%s linha %d ignorada: não está no formato CHAVE=VALOR.", caminho, numero)
            continue

        os.environ.setdefault(chave, _sem_aspas(valor.strip()))
        carregadas.append(chave)

    if carregadas:
        log.info("Carregado de %s: %s", caminho, ", ".join(carregadas))


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
