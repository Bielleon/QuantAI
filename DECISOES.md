# DECISOES.md — Registro de decisões técnicas do QuantAI

Toda decisão relevante entra aqui com data e justificativa. Mudanças nas
regras de negócio (seção 8 do plano) exigem aprovação explícita registrada.

---

## 2026-07-12 — FASE 0 (setup)

1. **Ordem de criação dos segredos:** `.gitignore` criado ANTES do `.env` e antes do
   `git init`, garantindo que nenhum segredo entre no histórico do git em momento algum.
   `PROMPT_MESTRE_QuantAI.md` e `data/` também fora do versionamento.

2. **Venv fora do OneDrive:** o projeto vive em `OneDrive\Desktop\QuantAI`; ambientes
   virtuais têm milhares de arquivos pequenos e conflitam com a sincronização do OneDrive.
   O venv fica em `%USERPROFILE%\venvs\quantai`. A replicabilidade é garantida pelo
   `requirements.txt` pinado, não pela pasta do venv.

3. **Acesso ao Supabase via PostgREST + requests (sem SDK):** cada operação é um HTTP
   explícito e auditável; menos dependências para pinar. `service_role` usada SOMENTE
   nos scripts locais (backend); `anon` reservada para futuro dashboard.

4. **RLS ligado em todas as tabelas, sem policies:** a chave `anon` não enxerga nada até
   criarmos policies de leitura (quando/se houver dashboard). A `service_role` ignora RLS
   por design do Supabase, então o pipeline não é afetado. Custo zero, segurança máxima.

5. **Failover Apify — nota de honestidade (exigida pelo plano):** empilhar cotas
   gratuitas de múltiplas contas fere os termos de uso da Apify. O failover em ordem
   existe para robustez operacional; se os créditos acabarem de verdade, a recomendação
   registrada é assinar o plano Starter com desconto de estudante (30%).

6. **Chaves Gemini no formato `AQ.`:** não é o formato clássico do AI Studio (`AIza...`).
   O cliente (`utils/gemini_client.py`) testa os dois endpoints (generativelanguage e
   aiplatform). **Resultado do health check (2026-07-12):** as chaves `AQ.` funcionam no
   endpoint `generativelanguage.googleapis.com`; chaves 1 e 3 OK, chave 2 inválida (401 —
   humano precisa regenerar). Alias `gemini-flash-lite-latest` resolveu para
   **`gemini-3.1-flash-lite`**, gravado em `GEMINI_MODEL_PINNED` (usado daqui em diante).

7. **Criação das tabelas (DDL):** não há MCP do Supabase conectado nem senha do banco
   Postgres; a `service_role` só opera dados (PostgREST), não DDL. Gerado `db/schema.sql`
   idempotente. PENDENTE: humano roda no SQL Editor do Supabase OU fornece a connection
   string do banco para automatizarmos.

8. **Nome do CSV do universo:** o arquivo local chama-se `Analise de acoes confiaveis (1).csv`
   (com " (1)"); os scripts usam esse nome tal qual está na pasta.

9. **Simplificação declarada da renda fixa (para o relatório):** a perna RF rende a Selic
   diária acumulada (SGS 11); desconsideramos marcação a mercado da LFT (ágio/deságio) e
   taxa de custódia do Tesouro Direto (0,20% a.a.) — efeito marginal, declarado como limitação.

10. **Contador de custos:** `utils/custos.py` registra eventos (tokens Gemini, runs Apify)
    em `data/contador_custos.json`; o acumulado é reportado ao fim de cada fase.
    Orçamento total: < R$ 300.

11. **Health check FASE 0 (2026-07-12):** 15 testes, 13 OK. Supabase service_role OK;
    7/7 tokens Apify OK (todos plano FREE, contas distintas — ver item 5); Gemini 2/3 OK;
    BCB (Selic 10/07/2026 = 0,052531%/dia), CVM IPE e yfinance (^BVSP) OK.
    Falhas: SUPABASE_ANON_KEY devolve 401 (possivelmente rotacionada no painel — não
    bloqueia o pipeline, que usa service_role) e chave Gemini 2/3 inválida (401).

12. **Revisão multi-agente da FASE 0 (2026-07-12):** 24 agentes revisaram os artefatos em
    4 dimensões (segredos, schema, bugs, regras) com verificação adversarial; 12 achados
    confirmados, todos corrigidos antes do 1º commit. Principais correções:
    - **Segredos nunca na URL:** tokens Apify e chaves Gemini agora vão em headers
      (Authorization / x-goog-api-key); mensagens de exceção do requests embutem a URL e
      vazariam a chave inteira. Além disso, `config.redigir()` mascara qualquer segredo
      em toda mensagem de erro (defesa em profundidade).
    - Health check agora FALHA se APIFY_TOKENS/GEMINI_API_KEYS estiverem ausentes no .env
      (antes, 0 chaves passava em silêncio com exit 0).
    - `db.selecionar()` exige `order` determinístico (paginação limit/offset sem ORDER BY
      pode duplicar/pular linhas silenciosamente).
    - Contador de custos virou JSONL com append (robusto a interrupção; antes, um Ctrl+C
      no meio da escrita corrompia o JSON e travava o pipeline).
    - Gemini: 5xx/rede persistente lança ServicoIndisponivelError SEM descartar chaves
      (antes virava falso "cota esgotada"); removido fallback silencioso para o alias
      -latest (agora falha ruidosamente se o modelo não estiver pinado).
    - Apify: 429 é rate-limit transitório -> retry com backoff no MESMO token; só 402
      (sem créditos) e 401 (token inválido) trocam de token.
    - `utils/config.py` agora contém TODAS as constantes travadas da seção 8 (%RV base
      60, sensibilidade 20, limites [40,80], período 2021-01..2026-06, temperature 0).
    - `flags_relatorio` ganhou modelo/model_version/prompt_hash e unique(doc_id) — o veto
      também é saída de LLM e precisa da mesma trilha de auditoria do sentimento.

---

## 2026-07-19 — FASE 1 (universo)

13. **Nome do robô definido pelo humano:** Hermes (usado nas cartas mensais da seção 9.3).

14. **DDL aplicado via MCP do Supabase** (`apply_migration`, migração `fase0_schema_inicial`),
    resolvendo a pendência da FASE 0. O projeto estava INACTIVE (pausa automática do plano
    gratuito após dias sem uso) e foi restaurado antes — atenção: isso pode se repetir em
    períodos de inatividade; basta restaurar no painel ou via MCP.

15. **Limpeza do CSV do universo (autorizada pelo humano: "pode excluir o que não achar
    necessário"):**
    - 2 linhas órfãs descartadas (sem ticker/nome, só com um volume solto — resíduo de
      edição manual da planilha);
    - colunas `nivel_governanca`, `aprovado_final` e `observacoes` NÃO importadas
      (100% vazias em todas as linhas);
    - coluna `volume` mantida no jsonb como `volume_medio` (informação de liquidez);
    - `roe_desvio_padrao_%` está zerado em TODAS as linhas (não foi calculado de fato) —
      mantido no jsonb por fidelidade ao arquivo original, mas **não pode ser usado em
      análise** — limitação declarada para o relatório;
    - setores em inglês normalizados para o padrão pt-BR do próprio CSV (Finance→Serviços
      Financeiros p/ ITSA4, Producer Manufacturing→Bens Industriais p/ POMO4,
      Utilities→Energia p/ ALUP11), original preservado em `setor_original` no jsonb.

16. **Empresas com 2 classes de ação no CSV (Petrobras: PETR3+PETR4; Itaú: ITUB3+ITUB4):**
    importadas as 4 linhas, mas PETR3 e ITUB3 ficam com `ativo=false` (motivo no jsonb).
    Razão: o modelo é 1 empresa = 1 fluxo de notícias; duas classes da mesma empresa
    duplicariam o sinal de sentimento. Mantidas as classes MAIS líquidas (PETR4: 27,3M
    vs 9,7M; ITUB4: 28,8M vs 1,8M de volume), que são as da tabela de apelidos da equipe.
    Universo ativo final: 20 tickers.

17. **Data-base das métricas do CSV (viés de sobrevivência — resposta do humano):** o
    humano informa que as métricas cobrem "apenas 5 anos" das empresas da lista, mas o
    CSV registra `anos_disponiveis=10` em todas as linhas — divergência anotada como
    limitação declarada: o filtro de qualidade foi feito pela equipe com dados que
    incluem o período recente, logo o universo carrega viés de sobrevivência/seleção
    (empresas escolhidas por terem sido boas ATÉ HOJE). Vai como limitação no relatório
    final. (Se a equipe esclarecer a janela exata, atualizar aqui.)

18. **Validação yfinance do universo (2026-07-19):** 20 tickers ativos + ^BVSP verificados.
    - **Emendas CCRO3→MOTV3 e TRPL4→ISAE4 NÃO são necessárias:** o yfinance preserva o
      histórico completo sob o ticker novo (MOTV3.SA e ISAE4.SA têm série desde 2020-12-01).
      A previsão do plano (seção 4) foi testada e o caminho simples venceu.
    - **IGTI11 só tem série a partir de 2021-11-22** (reestruturação Iguatemi/Jereissati
      criou a unit IGTI11 em nov/2021; antes o ticker era IGTA3). IGTA3.SA está deslistado
      do yfinance (série vazia) — emenda impossível pela mesma fonte. **APROVADO pelo
      humano em 2026-07-19:** IGTI11 fica inelegível nos meses sem preço (fev–nov/2021),
      entrando no backtest a partir de dez/2021; registrado como limitação declarada.

---

## 2026-07-19 — FASE 2 (notícias GDELT)

19. **Inspeção do noticias_finais.csv:** 13.455 linhas, 0 URLs duplicadas, 0 datas
    inválidas, 11 veículos (InfoMoney/Globo/Estadão/UOL/Exame dominam), campo `tom`
    100% parseável. Mapeamento APROVADO pelo humano em 2026-07-19:
    DATE(AAAAMMDDHHMMSS, UTC)→data_pub; url→url (dedup); fonte(domínio)→veiculo;
    tom (1º dos 7 valores)→tom_gdelt; fonte da tabela='gdelt'; título extraído do slug
    da URL (95,7% legíveis; 574 ilegíveis ficam com titulo NULL para o Cheerio/FASE 3).
    Colunas temas/locais/organizacoes/arquivo_origem NÃO importadas (modelo não usa;
    preservadas no CSV versionado).

20. **MUDANÇA DE REGRA TRAVADA (seção 8) — APROVADA explicitamente pelo humano em
    2026-07-19:** o GDELT só tem dados a partir de 12/07/2021 (jan–jun/2021 = zero
    notícias, sem base de sinal). Novo período: **sinais a partir de ago/2021 (1º mês
    completo de mídia), primeira carteira em set/2021**; fim inalterado (jun/2026).
    Motivo: carteira sem sinal não é defensável perante a banca. Vira limitação
    declarada no relatório ("dados de mídia disponíveis a partir de jul/2021").
    `config.BACKTEST_INICIO` atualizado de '2021-01' para '2021-08'.

21. **Importação GDELT executada + revisão adversarial (8 achados confirmados, todos
    tratados):** 13.455 notícias importadas (idempotente: 2ª execução insere 0).
    O extrator de títulos por slug foi endurecido após a revisão:
    - ids numéricos só são removidos nas BORDAS do slug — no meio são valores da
      manchete (ex.: 'lucro-r-44561-bilhoes' = R$ 445,61 bi; antes o valor sumia e o
      título enganoso iria para o LLM);
    - padrão antigo do Estadão ('secao,slug,id') tratado; datas no fim de URLs
      (Reuters/Estadão) descartadas; 'NNpercent' vira 'NN%'; espaços normalizados.
    - **Limitação declarada:** o separador decimal não existe no slug ('067percent' =
      0,67% vira '067%'); ~190 manchetes têm número com decimal ambíguo. Mitigação
      possível na FASE 3 (buscar título real via Cheerio também para essas).
    - `db.selecionar(limite>1000)` truncava silenciosamente em 1000 (max-rows do
      PostgREST); agora pagina até o limite e para em página vazia (robusto a
      mudança do cap do servidor).
    - Sem título via slug: 446 URLs (3,3%) — candidatas ao Cheerio na FASE 3. O número
      subiu de 248 para 446 de propósito: slugs que ficaram genéricos demais após a
      limpeza (ex.: 'melhores acoes ibovespa' sem a data) agora vão buscar o título
      real em vez de entrar com manchete adivinhada.

## Pendências abertas
- [x] DDL no Supabase — resolvido em 2026-07-19 via MCP (item 14).
- [x] Data-base das métricas do CSV — respondido pelo humano em 2026-07-19 (item 17).
- [x] `NOME_DO_ROBO` — Hermes (item 13).
- [x] GitHub — resolvido em 2026-07-19: login via device flow (conta Bielleon),
      repositório PRIVADO criado em https://github.com/Bielleon/QuantAI, push ok.
- [ ] SUPABASE_ANON_KEY inválida (401) — recopiar do painel quando formos fazer dashboard.
- [ ] Chave Gemini 2/3 inválida (401) — regenerar no AI Studio/console.
