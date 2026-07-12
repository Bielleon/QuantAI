"""FASE 4 — Documentos CVM (IPE) e extrator de veto.

Baixa ipe_cia_aberta_2021..2026.zip (CSV ; latin-1), filtra empresas do
universo + categorias {'Fato Relevante','Comunicado ao Mercado'}, baixa os
PDFs, extrai texto (pdfplumber; escaneado => marca status e pula), roda o
EXTRATOR DE VETO (prompt congelado 9.2, temperature 0, modelo pinado,
prompt_hash sha256 gravado em flags_relatorio junto com modelo/model_version)
e valida a citação literal POR CÓDIGO (se não for substring exata do texto,
a flag é REJEITADA: validacao_literal=false, sem veto).
Documentos CVM NÃO geram sentimento — só veto. Idempotente por link_download
(documentos) e por doc_id (flags).
"""
# TODO(FASE 4): implementar após OK da FASE 3.

if __name__ == "__main__":
    raise SystemExit("FASE 4 ainda não implementada.")
