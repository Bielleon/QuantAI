"""Contador de custos estimados do projeto (orçamento total: < R$ 300).

Registra eventos em data/contador_custos.jsonl (fora do git) — formato JSONL:
1 linha JSON por evento, gravada com append. Append de linha única é robusto
a interrupções (Ctrl+C, queda de energia, sync do OneDrive): no pior caso a
última linha fica truncada e é ignorada na leitura, sem corromper o restante.
"""
import json
from datetime import datetime, timezone

from . import config

_ARQUIVO = config.DATA_DIR / "contador_custos.jsonl"

# Estimativas unitárias em R$ (atualizar se os preços mudarem):
# - gemini_tokens: cota gratuita → custo monetário 0 (contamos tokens p/ auditoria)
# - apify_run: varia por actor; o custo real é registrado por fase no DECISOES.md
CUSTO_UNITARIO_BRL = {"gemini_tokens": 0.0, "apify_run": 0.0}


def registrar(evento: str, quantidade: float, detalhe: str = "", custo_brl: float | None = None) -> None:
    registro = {
        "quando": datetime.now(timezone.utc).isoformat(),
        "evento": evento,
        "quantidade": quantidade,
        "custo_brl": custo_brl if custo_brl is not None else CUSTO_UNITARIO_BRL.get(evento, 0.0) * quantidade,
        "detalhe": detalhe,
    }
    with _ARQUIVO.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _carregar() -> list[dict]:
    if not _ARQUIVO.exists():
        return []
    registros = []
    for linha in _ARQUIVO.read_text(encoding="utf-8").splitlines():
        try:
            registros.append(json.loads(linha))
        except json.JSONDecodeError:
            continue  # linha truncada por interrupção — ignorada, resto do arquivo permanece válido
    return registros


def resumo() -> dict:
    registros = _carregar()
    por_evento: dict[str, float] = {}
    total = 0.0
    for reg in registros:
        por_evento[reg["evento"]] = por_evento.get(reg["evento"], 0.0) + reg["quantidade"]
        total += reg["custo_brl"]
    return {"por_evento": por_evento, "custo_total_brl": round(total, 2)}
