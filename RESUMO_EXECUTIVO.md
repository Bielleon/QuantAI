# RESUMO EXECUTIVO — QuantAI (robô Hermes)

Período do backtest: **2021-01 a 2026-06** (decisão mensal, execução no 1º pregão do mês seguinte).

Corpus de mídia: **35472 notícias** com título; **250 classificadas** (1%).

## Resultados vs. benchmarks

| estratégia | retorno total | CAGR | vol. anual | Sharpe | max. drawdown | turnover médio |
|---|---|---|---|---|---|---|
| **Hermes (contrária)** | 107.1% | 14.4% | 4.9% | 0.58 | 0.0% | 4.0% |
| **Hermes (invertida)** | 103.7% | 14.1% | 4.4% | 0.57 | 0.0% | 3.3% |
| Ibovespa | 46.3% | 7.3% | 18.0% | -0.12 | -22.9% | 0.0% |
| Tesouro Selic | 79.5% | 11.4% | 1.0% | 0.00 | 0.0% | 0.0% |
| 60/40 (Ibov/Selic) | 62.0% | 9.3% | 10.9% | -0.12 | -12.4% | 0.0% |

## Retorno ano a ano

| ano | contrária | invertida | Ibovespa | Selic |
|---|---|---|---|---|
| 2021 | 7.7% | 6.8% | -14.1% | 3.5% |
| 2022 | 24.1% | 23.1% | 10.1% | 12.0% |
| 2023 | 13.0% | 13.0% | 15.5% | 13.3% |
| 2024 | 10.9% | 10.9% | -2.2% | 10.8% |
| 2025 | 14.3% | 14.3% | 26.7% | 14.0% |
| 2026 | 8.1% | 8.1% | 8.2% | 8.1% |

## Vetos aplicados (1)

| empresa | risco | evento | veto de | veto até | citação (validada por código) |
|---|---|---|---|---|---|
| MOTV3 | intervencao_regulatoria_grave | 2021-10-01 | 2021-10-01 | 2022-04-01 | teve conhecimento de decisão cautelar emitida nesta data pelo Tribunal de Contas do Estado do Paraná (“TCE/PR”… |

## Limitações declaradas

- **Renda fixa simplificada:** a perna RF rende a Selic diária acumulada; desconsideramos marcação a mercado da LFT e a taxa de custódia (0,20% a.a.).
- **Viés de sobrevivência:** o universo foi filtrado pela equipe com dados que incluem o período recente; empresas escolhidas por terem sido boas até hoje.
- **Cobertura de mídia crescente:** de ~11/20 empresas elegíveis em 2021 para 20/20 em 2026 (indexação mais densa para conteúdo recente); a carteira é mais concentrada no início do período.
- **Pré-filtro de veto:** documentos da CVM sem nenhum termo de risco não foram enviados ao LLM (economia de cota); auditoria adversarial não encontrou falso negativo.
- **Sem OCR:** 34 documentos escaneados não tiveram texto extraído.
- **2 pregões ausentes** na fonte de preços para 8 tickers (forward-fill).

## Próximos passos sugeridos

- Rotular o `gabarito.csv` (150 manchetes) para medir a acurácia do classificador.
- Testar sensibilidade do limiar de elegibilidade e do teto de 15% por ação.
- Avaliar uso da tendência numérica (hoje só auditada) como filtro adicional.
