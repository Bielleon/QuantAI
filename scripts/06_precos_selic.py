"""FASE 6 — Preços ajustados e taxa livre de risco.

yfinance: fechamento ajustado 2020-12 -> hoje de todos os tickers .SA + ^BVSP
(com emendas CCRO3->MOTV3 e TRPL4->ISAE4 registradas). BCB SGS série 11:
Selic diária. Grava em `precos` e `taxa_rf`; verifica buracos nas séries.
Idempotente: upsert por (ticker, data) / (data).
"""
# TODO(FASE 6): implementar após OK da FASE 5.

if __name__ == "__main__":
    raise SystemExit("FASE 6 ainda não implementada.")
