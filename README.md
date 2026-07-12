# QuantAI — Desafio Itaú Asset Quant AI 2026

Estratégia quantitativa que combina **sentimento de notícias** (classificado por LLM),
**veto fundamentalista** (documentos oficiais da CVM) e **backtest próprio** (2021–2026,
decisão mensal, duas variantes: aposta contrária e momentum).

## Arquitetura (filosofia da fusão de sinais)
1. **Notícia julga sentimento** — mídia classificada pelo Gemini (prompt congelado, temperature 0).
2. **Relatório oficial (CVM) extrai fatos** — atua SOMENTE como veto de risco grave, com citação literal validada por código.
3. **Números são processados por código** — nenhum cálculo passa pela LLM.
4. **Veto manda mais que sentimento.**
5. **Persistência auditável** — cada posição mensal grava as variáveis separadas.

## Estrutura
```
db/schema.sql          # schema do Supabase (Postgres)
utils/                 # config, acesso ao banco, clientes Gemini/Apify (failover), custos
scripts/00..08 + run_all.py   # pipeline por fase
data/                  # dados brutos (fora do git)
outputs/               # gráficos, tabelas, resumo executivo
DECISOES.md            # registro de decisões técnicas
```

## Reprodução
1. Python 3.12; crie um venv e instale as dependências pinadas:
   `python -m venv %USERPROFILE%\venvs\quantai && %USERPROFILE%\venvs\quantai\Scripts\pip install -r requirements.txt`
   (venv fora da pasta do projeto porque ela está no OneDrive — evita conflito de sincronização)
2. Crie `.env` na raiz com as chaves (ver `.env.exemplo` mental na documentação da equipe — nunca versionado).
3. Rode `db/schema.sql` no Supabase (SQL Editor) uma única vez.
4. Execute as fases em ordem: `python scripts/00_health_check.py`, depois `01`, `02`, ...
   Scripts que gastam créditos (03, 05, 07) pedem confirmação humana antes.

## Replicabilidade
- Modelo Gemini **pinado** em `GEMINI_MODEL_PINNED` (nunca alias `-latest` em produção).
- Prompts congelados (seção 9 do plano) com `prompt_hash` sha256 gravado por classificação.
- `requirements.txt` com versões exatas.
- Backtest com teste automatizado anti-look-ahead.
