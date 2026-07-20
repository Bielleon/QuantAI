"""Acesso ao Supabase via PostgREST (API REST), sempre com a service_role.

Escolha deliberada: usamos requests puro (em vez do SDK supabase-py) para o
código ficar transparente e auditável — cada operação é um HTTP simples.
"""
import requests

from . import config

_TIMEOUT = 60


def _headers(prefer: str | None = None) -> dict:
    h = {
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _url(tabela: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{tabela}"


def selecionar(tabela: str, params: dict | None = None, order: str | None = None,
               limite: int | None = None) -> list[dict]:
    """SELECT com filtros PostgREST. Pagina automaticamente (lotes de 1000).

    `order` é OBRIGATÓRIO (ex.: 'id' ou 'ticker,data'): sem ORDER BY o Postgres
    não garante ordem estável entre páginas — linhas poderiam repetir ou sumir
    silenciosamente entre uma página e outra.
    `limite`: se informado, devolve no máximo N linhas (paginando se N > 1000,
    pois o PostgREST corta cada requisição em max-rows=1000 SEM avisar).
    """
    params = dict(params or {})
    if order:
        params["order"] = order
    if "order" not in params:
        raise ValueError("selecionar() exige 'order' determinístico (ex.: chave primária) para paginação segura")
    resultado: list[dict] = []
    offset = 0
    while True:
        pagina = 1000 if limite is None else min(1000, limite - len(resultado))
        if pagina <= 0:
            return resultado
        params["limit"] = pagina
        params["offset"] = offset
        r = requests.get(_url(tabela), headers=_headers(), params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        lote = r.json()
        resultado.extend(lote)
        # página vazia = fim dos dados (robusto mesmo se o max-rows do servidor mudar)
        if not lote or (limite is not None and len(resultado) >= limite):
            return resultado
        offset += len(lote)


def upsert(tabela: str, linhas: list[dict], on_conflict: str) -> int:
    """INSERT ... ON CONFLICT DO UPDATE (idempotente). Devolve nº de linhas enviadas."""
    if not linhas:
        return 0
    r = requests.post(
        _url(tabela) + f"?on_conflict={on_conflict}",
        headers=_headers(prefer="resolution=merge-duplicates,return=minimal"),
        json=linhas,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return len(linhas)


def inserir_ignorando_duplicatas(tabela: str, linhas: list[dict], on_conflict: str) -> int:
    """INSERT ... ON CONFLICT DO NOTHING (idempotente sem sobrescrever)."""
    if not linhas:
        return 0
    r = requests.post(
        _url(tabela) + f"?on_conflict={on_conflict}",
        headers=_headers(prefer="resolution=ignore-duplicates,return=minimal"),
        json=linhas,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return len(linhas)


def atualizar(tabela: str, params: dict, valores: dict) -> None:
    """UPDATE nas linhas que casam com os filtros PostgREST."""
    r = requests.patch(
        _url(tabela), headers=_headers(prefer="return=minimal"),
        params=params, json=valores, timeout=_TIMEOUT,
    )
    r.raise_for_status()


def contar(tabela: str, params: dict | None = None) -> int:
    """COUNT exato via cabeçalho Content-Range."""
    h = _headers(prefer="count=exact")
    h["Range-Unit"] = "items"
    h["Range"] = "0-0"
    r = requests.get(_url(tabela), headers=h, params=params or {}, timeout=_TIMEOUT)
    r.raise_for_status()
    faixa = r.headers.get("Content-Range", "/0")
    return int(faixa.split("/")[-1])
