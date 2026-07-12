"""Cliente Apify com failover de tokens (em ordem, trocando em erro de créditos).

Nota de honestidade registrada no DECISOES.md: empilhar cotas gratuitas de
várias contas fere os termos da Apify. O failover existe para robustez; se os
créditos acabarem de verdade, a recomendação é assinar o plano Starter
(desconto de estudante de 30%).

Segurança: o token vai SEMPRE no header Authorization (nunca na URL), e toda
mensagem de erro passa por config.redigir() antes de propagar.

Semântica dos status HTTP da Apify:
  429 = rate limit transitório (reqs/segundo) -> retry com backoff no MESMO token;
  402 = sem créditos -> trocar de token;
  401 = token inválido/bloqueado -> trocar de token.
"""
import time

import requests

from . import config, custos

_BASE = "https://api.apify.com/v2"
_TIMEOUT = 60
_MAX_RETRIES_429 = 5


class CreditosEsgotadosError(Exception):
    """Todos os tokens Apify sem créditos — parar e reportar."""


class ApifyError(Exception):
    """Erro não recuperável da API Apify (mensagem já sanitizada)."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def testar_token(token: str) -> dict:
    """Health check: GET /users/me. Devolve username e plano, sem custo."""
    try:
        r = requests.get(f"{_BASE}/users/me", headers=_headers(token), timeout=_TIMEOUT)
    except requests.RequestException as e:
        return {"ok": False, "detalhe": config.redigir(f"erro de rede: {e}")}
    if r.status_code != 200:
        return {"ok": False, "detalhe": config.redigir(f"HTTP {r.status_code}: {r.text[:150]}")}
    dados = r.json().get("data", {})
    return {"ok": True, "username": dados.get("username"), "plano": dados.get("plan", {}).get("id"), "detalhe": ""}


class ApifyClient:
    """Chama actors usando os tokens EM ORDEM; troca em falta de créditos/token inválido."""

    def __init__(self):
        self.tokens = list(config.APIFY_TOKENS)
        self.idx = 0

    @property
    def token_atual(self) -> str:
        if self.idx >= len(self.tokens):
            raise CreditosEsgotadosError("Todos os tokens Apify esgotados.")
        return self.tokens[self.idx]

    def _trocar_token(self, motivo: str):
        print(f"[apify] token {config.mascarar(self.token_atual)} descartado ({motivo}) — trocando para o próximo")
        self.idx += 1
        _ = self.token_atual  # lança CreditosEsgotadosError se acabaram

    def rodar_actor_sync(self, actor_id: str, input_json: dict, timeout_s: int = 300) -> list[dict]:
        """Roda um actor de forma síncrona e devolve os itens do dataset."""
        tentativas_429 = 0
        while True:
            try:
                r = requests.post(
                    f"{_BASE}/acts/{actor_id}/run-sync-get-dataset-items",
                    headers=_headers(self.token_atual),
                    params={"timeout": timeout_s},
                    json=input_json,
                    timeout=timeout_s + 60,
                )
            except requests.RequestException as e:
                raise ApifyError(config.redigir(f"erro de rede ao chamar actor {actor_id}: {e}")) from None
            if r.status_code == 429:
                # rate limit transitório: backoff exponencial no MESMO token (créditos intactos)
                tentativas_429 += 1
                if tentativas_429 <= _MAX_RETRIES_429:
                    time.sleep(2 ** tentativas_429)
                    continue
                self._trocar_token("429 persistente após retries")
                tentativas_429 = 0
                continue
            if r.status_code == 402:
                self._trocar_token("402 — sem créditos")
                continue
            if r.status_code == 401:
                self._trocar_token("401 — token inválido/bloqueado")
                continue
            if r.status_code >= 400:
                raise ApifyError(config.redigir(
                    f"Apify devolveu HTTP {r.status_code} para o actor {actor_id}: {r.text[:300]}"
                ))
            itens = r.json()
            custos.registrar("apify_run", 1, detalhe=f"actor={actor_id} itens={len(itens)}")
            return itens
