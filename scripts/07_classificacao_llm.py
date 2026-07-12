"""FASE 7 — Classificação de sentimento via Gemini (prompt congelado 9.1).

Piloto: 200 manchetes -> mostrar distribuição -> OK humano -> lote completo.
25 manchetes/chamada, temperature 0, modelo PINADO, idempotente (só notícias
sem sentimento), checkpoint a cada lote, parada limpa em 429 diário, failover
de chaves. Exporta gabarito.csv (150 manchetes estratificadas) p/ rotulagem
humana. prompt_hash = sha256 do prompt congelado.
"""
# TODO(FASE 7): implementar após OK da FASE 6.

if __name__ == "__main__":
    raise SystemExit("FASE 7 ainda não implementada.")
