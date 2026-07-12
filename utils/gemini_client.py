"""Cliente Gemini com failover de chaves e modelo pinado.

Suporta dois endpoints, porque as chaves no formato 'AQ.' são do modo
Express da Vertex AI (não o formato clássico 'AIza...' do AI Studio):
  - AI Studio:      generativelanguage.googleapis.com
  - Vertex Express: aiplatform.googleapis.com
O health check descobre qual endpoint cada chave aceita. Health check de
2026-07-12: as chaves AQ. deste projeto funcionam no generativelanguage.

Segurança: a chave vai SEMPRE no header x-goog-api-key (nunca na URL), e
toda mensagem de erro passa por config.redigir() — assim nenhuma exceção
carrega chave inteira para console ou log.

Failover: chaves usadas EM ORDEM; SÓ erro de cota (429 persistente) troca
de chave. Instabilidade do serviço (5xx/rede) NÃO descarta chaves — lança
ServicoIndisponivelError. Esgotadas todas as chaves: CotaEsgotadaError
(parada limpa — o chamador salva checkpoint e encerra).
"""
import time

import requests

from . import config, custos

ALIAS_INICIAL = "gemini-flash-lite-latest"  # usado SÓ no health check, para descobrir o modelVersion
_MODELOS_CANDIDATOS = [ALIAS_INICIAL, "gemini-2.5-flash-lite", "gemini-2.0-flash-lite-001"]
_TIMEOUT = 120


class CotaEsgotadaError(Exception):
    """Todas as chaves Gemini atingiram a cota — parar de forma limpa."""


class ServicoIndisponivelError(Exception):
    """Gemini instável (5xx/rede persistente). As chaves NÃO foram descartadas — tentar mais tarde."""


def _url(endpoint: str, modelo: str) -> str:
    if endpoint == "gl":
        return f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    return f"https://aiplatform.googleapis.com/v1/publishers/google/models/{modelo}:generateContent"


def _chamar(chave: str, endpoint: str, modelo: str, prompt: str, temperature: float) -> requests.Response:
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    # chave no header (nunca na URL: exceções do requests embutem a URL na mensagem)
    return requests.post(_url(endpoint, modelo), headers={"x-goog-api-key": chave},
                         json=corpo, timeout=_TIMEOUT)


def testar_chave(chave: str) -> dict:
    """Health check: 1 chamada mínima. Descobre endpoint e modelVersion da chave."""
    ultimo_erro = ""
    for endpoint in ("gl", "vertex"):
        for modelo in _MODELOS_CANDIDATOS:
            try:
                r = _chamar(chave, endpoint, modelo, "Responda apenas: ok", 0.0)
            except requests.RequestException as e:
                ultimo_erro = config.redigir(f"erro de rede: {e}")
                continue
            if r.status_code == 200:
                dados = r.json()
                return {
                    "ok": True,
                    "endpoint": endpoint,
                    "modelo_pedido": modelo,
                    "model_version": dados.get("modelVersion", ""),
                    "detalhe": "",
                }
            ultimo_erro = config.redigir(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code == 404:
                continue  # modelo não existe neste endpoint — tenta o próximo
            break  # 401/403/429 etc.: trocar de endpoint, não de modelo
    return {"ok": False, "endpoint": None, "modelo_pedido": None, "model_version": None, "detalhe": ultimo_erro}


class GeminiClient:
    """Gera texto com o modelo PINADO, failover de chaves e contagem de tokens."""

    def __init__(self, endpoints_por_chave: dict[str, str] | None = None):
        self.chaves = list(config.GEMINI_API_KEYS)
        # endpoint de cada chave ('gl' ou 'vertex'); health check confirmou 'gl' p/ as chaves AQ.
        self.endpoints = endpoints_por_chave or {c: "gl" for c in self.chaves}
        self.idx = 0

    def gerar(self, prompt: str, temperature: float | None = None,
              max_tentativas_por_chave: int = 3) -> dict:
        """Devolve {'texto': ..., 'model_version': ...}.

        Lança: CotaEsgotadaError (todas as chaves no limite),
        ServicoIndisponivelError (5xx/rede persistente — chaves preservadas),
        RuntimeError (4xx não recuperável ou modelo não pinado).
        """
        if temperature is None:
            temperature = config.LLM_TEMPERATURE
        if not config.GEMINI_MODEL_PINNED:
            raise RuntimeError(
                "GEMINI_MODEL_PINNED vazio no .env — rode scripts/00_health_check.py antes. "
                "Regra travada: nunca usar o alias -latest em produção (replicabilidade)."
            )
        modelo = config.GEMINI_MODEL_PINNED

        while self.idx < len(self.chaves):
            chave = self.chaves[self.idx]
            motivo_falha = ""
            for tentativa in range(max_tentativas_por_chave):
                try:
                    r = _chamar(chave, self.endpoints.get(chave, "gl"), modelo, prompt, temperature)
                except requests.RequestException as e:
                    motivo_falha = "rede"
                    print(config.redigir(f"[gemini] erro de rede (tentativa {tentativa + 1}): {e}"))
                    time.sleep(3 * (tentativa + 1))
                    continue
                if r.status_code == 200:
                    dados = r.json()
                    uso = dados.get("usageMetadata", {})
                    custos.registrar("gemini_tokens", uso.get("totalTokenCount", 0),
                                     detalhe=f"chave={config.mascarar(chave)}")
                    candidatos = dados.get("candidates") or []
                    partes = (candidatos[0].get("content", {}).get("parts", []) if candidatos else [])
                    texto = "".join(p.get("text", "") for p in partes)
                    return {"texto": texto, "model_version": dados.get("modelVersion", modelo)}
                if r.status_code == 429:
                    motivo_falha = "cota"
                    time.sleep(5 * (tentativa + 1))
                    continue
                if r.status_code >= 500:
                    motivo_falha = "servico"
                    time.sleep(3 * (tentativa + 1))
                    continue
                # 4xx não recuperável (400/401/403): não é cota — erro sanitizado, sem retry
                raise RuntimeError(config.redigir(
                    f"Gemini devolveu HTTP {r.status_code} (chave {config.mascarar(chave)}): {r.text[:300]}"
                ))
            if motivo_falha != "cota":
                # 5xx/rede persistente: NÃO descartar a chave — o problema é do serviço
                raise ServicoIndisponivelError(
                    "Gemini instável (5xx/erro de rede persistente). Chaves preservadas — tente mais tarde."
                )
            print(f"[gemini] chave {config.mascarar(chave)} com cota estourada — trocando para a próxima")
            self.idx += 1
        raise CotaEsgotadaError("Todas as chaves Gemini atingiram o limite. Parada limpa.")
