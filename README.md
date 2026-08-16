# QuantAI — Desafio Itaú Asset Quant AI 2026

Estratégia quantitativa que combina **sentimento de notícias** (classificado por LLM),
**veto fundamentalista** (documentos oficiais da CVM) e **backtest próprio** — jan/2021 a
jun/2026, decisão mensal, em duas variantes: aposta contrária e momentum.
O robô gestor se chama **KRON** (Kernel de Rastreamento de Otimismo e Notícias).

## Filosofia da fusão de sinais

1. **Notícia julga sentimento.** Só a mídia mede percepção de mercado — classificada pelo
   Gemini com prompt congelado e `temperature 0`.
2. **Relatório oficial (CVM) extrai fatos.** Atua EXCLUSIVAMENTE como veto de risco grave;
   nunca gera sentimento (evita viés de PR corporativo). Toda flag exige **citação literal
   validada por código** — se a citação não for substring exata do documento, a flag é
   rejeitada e nenhum veto é aplicado.
3. **Números são processados por código.** Nenhum cálculo ou tendência passa pela LLM.
4. **Veto manda mais que sentimento.** Empresa com risco grave ativo fica fora da carteira
   por 6 meses, por melhor que seja a notícia.
5. **Persistência auditável.** Cada posição mensal grava as variáveis SEPARADAS:
   `sentimento_noticias`, `n_noticias`, `flags_relatorio`, `tendencia_numerica`,
   `classificacao_final`.

## O modelo em uma tela

| parâmetro | valor |
|---|---|
| Período | jan/2021 → jun/2026, decisão mensal |
| Alocação (contrária) | `%RV = 60 − 20 × S_mercado`, limitado a [40%, 80%] |
| Alocação (invertida) | `%RV = 60 + 20 × S_mercado`, pesos por rank invertido |
| Sentimento | `S = (n_pos − n_neg) / (n_pos + n_neg + n_neu)` ∈ [−1, +1] |
| Elegibilidade | ≥ 10 notícias de mídia no mês |
| Pesos | ∝ (N+1−rank) por S, teto de 15%/ação, renormalizado |
| Veto | 6 meses a partir do mês do evento |
| Custos | 0,1% sobre o turnover a cada rebalanceamento |
| Timing | sinais até o fim de M; execução no 1º pregão de M+1 |
| Renda fixa | Selic diária acumulada (mesma série do Sharpe e do 60/40) |

## Estrutura

```
db/schema.sql          schema do Supabase (10 tabelas, RLS ligado)
utils/                 config (regras travadas), db (PostgREST), clientes
                       Gemini/Apify com failover, contador de custos
scripts/00..08         pipeline por fase + run_all.py
  00_health_check         valida todas as chaves e fontes
  01_universo             importa e valida o universo investível
  02_gdelt_noticias       importa o histórico do GDELT
  03_titulos_reais        busca títulos reais das URLs sem manchete
  04_cvm_ipe              CVM: metadados → PDFs → pré-filtro → veto
  05_google_news          coleta empresa × mês via RSS
  06_precos_selic         preços ajustados + Selic diária
  07_classificacao_llm    sentimento (prompt congelado 9.1)
  08_backtest             indicadores, backtest, gráficos, cartas, resumo
  supervisor_*            relançam as etapas de LLM após cota/instabilidade
  relatorio_cobertura     notícias por empresa × mês
tests/test_backtest.py 58 verificações do motor com dados sintéticos
outputs/               gráficos PNG, métricas, auditoria, gabarito
DECISOES.md            registro de TODAS as decisões técnicas (fonte da verdade)
RESUMO_EXECUTIVO.md    números finais, vetos e limitações declaradas
```

## Reprodução

1. **Python 3.12** + venv (fora da pasta do projeto, que fica no OneDrive):
   ```
   python -m venv %USERPROFILE%\venvs\quantai
   %USERPROFILE%\venvs\quantai\Scripts\pip install -r requirements.txt
   ```
2. **`.env`** na raiz (nunca versionado) com `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `APIFY_TOKENS`, `GEMINI_API_KEYS`, `GEMINI_MODEL_PINNED`.
3. **Banco:** rode `db/schema.sql` no SQL Editor do Supabase (uma vez).
4. **Pipeline:** `python scripts/00_health_check.py` e depois `01`, `02`, ... em ordem,
   ou `python scripts/run_all.py`. Todos os scripts são **idempotentes** — reexecutar não
   duplica dados nem regasta cota.
5. **Testes:** `python tests/test_backtest.py` (não precisa de banco nem rede).

## Garantias de replicabilidade

- **Modelo pinado** em `GEMINI_MODEL_PINNED` (`gemini-3.1-flash-lite`); o alias `-latest`
  nunca é usado em produção — o cliente falha ruidosamente se o pin estiver vazio.
- **Prompts congelados** com `prompt_hash` sha256 gravado em cada classificação.
- **`requirements.txt`** com versões exatas.
- **Seeds fixas** onde há amostragem (gabarito, embaralhamento de filas).
- **Teste anti-look-ahead** roda como parte do backtest e falha a execução se violado.
- **Desempates determinísticos** (por ticker) para que a ordem do banco não afete o resultado.

## Custos

O projeto roda com **custo monetário zero**: dados abertos da CVM e do Banco Central,
yfinance, coleta local de notícias e cota gratuita do Gemini. O contador em
`utils/custos.py` registra o consumo acumulado. Orçamento autorizado: R$ 300.

## Limitações declaradas

Estão listadas no `RESUMO_EXECUTIVO.md` e detalhadas no `DECISOES.md`. Em resumo: renda
fixa simplificada (sem marcação a mercado da LFT nem custódia), viés de sobrevivência no
universo, cobertura de mídia crescente ao longo do tempo, pré-filtro de veto por palavras-chave
(auditado, sem falso negativo encontrado), ausência de OCR para 34 documentos escaneados e
2 pregões ausentes na fonte de preços para 8 tickers.
