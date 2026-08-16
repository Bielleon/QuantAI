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

---

## 2026-07-20 — FASE 3 (títulos reais)

22. **Troca do apify/cheerio-scraper por coleta local (requests + BeautifulSoup):**
    ao rodar o lote-teste, a API da Apify devolveu 403
    `full-permission-actor-not-approved` — o actor passou a exigir aprovação manual de
    permissões no console de CADA conta (ação que só o humano logado pode fazer, e
    seria necessária nas 7 contas do failover). Teste local nas mesmas URLs: 20/20
    títulos extraídos, custo R$ 0, sem depender do failover de cotas (item 5).
    DECISÃO: FASE 3 usa coletor local (`scripts/03_titulos_reais.py`); o actor fica
    como fallback documentado caso os sites passem a bloquear requisições diretas.
    Educação com servidores: 8 requisições paralelas no total, domínios intercalados,
    pausa de 0,1s por requisição. O humano aprovou a fase e o escopo (446 NULL + 193
    ambíguas); a troca de ferramenta mantém o resultado e elimina o custo.

23. **Supabase pausou pela 2ª vez em 2 dias** (free tier; a 1ª pausa foi após os 7 dias
    padrão, esta segunda fora do padrão). Restaurado via MCP. Se a frequência
    atrapalhar, considerar: manter o painel aberto durante o trabalho, ou plano Pro.
    Monitorando por ora.

24. **Execução da FASE 3 + revisão adversarial (8 achados confirmados, todos tratados):**
    626 títulos reais coletados (de 639 na fila), custo R$ 0. Correções pós-revisão:
    - **Encoding:** parse via bytes (BeautifulSoup detecta charset do meta) — páginas
      UTF-8 sem charset no header (Folha) corrompiam acentos; 6 títulos reparados.
    - **Sufixo de veículo** ('| G1', '| Exame INSIGHT', '- InfoMoney'...) e prefixo
      'Opinião |' agora sempre removidos; saneamento retroativo em 341 títulos.
    - **105 'Fórum dos Leitores' (Estadão) + 2 páginas-hub (InfoMoney)** anulados:
      compilações/listagens sem manchete própria — ficam fora da classificação.
    - **401/403/429 = 'bloqueada' (anti-bot)**, não 'morta': 8 URLs da Reuters existem
      mas não saem sem navegador — perda declarada (0,06% do corpus).
    - **Ponto-fixo na fila:** ambíguas só se o título for todo minúsculo (assinatura de
      slug) — título real corrigido nunca volta à fila, mesmo sem o log local.
    - **Persistência incremental** (grava resultado a resultado; interrupção não perde nada).
    - Estado final: **13.335/13.455 notícias com título (99,1%)**; 120 sem título
      (107 deliberados + 9 bloqueadas/mortas + 4 erros de rede persistentes após 3
      tentativas — regra das 3 falhas aplicada, paramos de insistir).

---

## 2026-08-12 — FASE 4 (CVM / veto)

25. **Metadados IPE:** 5.102 documentos das 20 empresas (1.171 Fatos Relevantes +
    3.931 Comunicados ao Mercado), 2021–2026. Ajustes de matching após auditoria dos
    nomes casados: **SBSP3 incluída** (a CVM registra "CIA SANEAMENTO BÁSICO ESTADO SÃO
    PAULO" — sem a palavra "Sabesp", o matching automático não alcançava: 308 docs
    seriam perdidos) e **2 falsos positivos do BTG excluídos** (BTG Pactual Commodities
    Sertrading e BTG Pactual Participations Ltd — entidades distintas da BPAC11).
    Renomeações confirmadas no casamento: CCR→MOTV3, CTEEP→ISAE4, Iguatemi Shopping→IGTI11.

26. **Três defeitos de infraestrutura encontrados e corrigidos durante a execução:**
    - *Processos duplicados:* o mesmo script rodava 2x em paralelo escrevendo os mesmos
      arquivos → PDFs truncados marcados como erro. Passei a garantir 1 processo por etapa.
    - *Travamento na extração:* PDFs patológicos congelavam o pdfplumber indefinidamente.
      Extração passou a rodar em subprocesso com watchdog (90s) e pypdfium2 como motor
      primário (mais rápido e tolerante), pdfplumber como fallback.
    - *Deadlock do watchdog (o mais grave):* o padrão `join()` antes de `get()` na fila do
      multiprocessing trava o filho quando o texto excede o buffer do pipe. Efeito
      perverso: descartava sistematicamente os documentos MAIS LONGOS — justamente os
      com maior chance de detalhar risco grave, enviesando o veto para documentos curtos.
      Diagnóstico com amostra de 12: **12/12 recuperados** com `get()` antes de `join()`.
      Corrigido (+ truncagem no filho) e os ~500 documentos afetados voltaram para a fila.

27. **Failover Gemini validado em produção:** chave 1 esgotou a cota diária → trocou;
    chave 2 (a inválida conhecida) devolveu 401 → **pulada** (antes o cliente a tratava
    como erro fatal; agora 401/403 pula para a próxima chave); chave 3 assumiu.

28. **Primeiro veto capturado (mecanismo completo funcionando):** MOTV3 (então CCR),
    Fato Relevante de 28/10/2021 — decisão cautelar do TCE/PR decretando inidoneidade das
    concessionárias no Paraná (caso RodoNorte). Classificado como
    `intervencao_regulatoria_grave`, **citação literal validada por código** contra o
    texto do PDF, veto aplicado de out/2021 a abr/2022 (VETO_MESES=6).

29. **Quarto defeito: HTML salvo como PDF (achado em 2026-08-12).** Sob carga, a CVM
    devolve **HTTP 200 com a página HTML "ENET Download de Documento"** em vez do PDF.
    O código só checava o status, então gravava ~4 KB de HTML num arquivo `.pdf`; a
    extração não achava texto e o documento virava `vazio_ou_escaneado` — e, como o
    arquivo existia em disco com tamanho > 0, nenhuma reexecução o rebaixava: perda
    **permanente e silenciosa** de 450 documentos (13% dos baixados).
    Diagnóstico: os arquivos começavam com `<!DOCTYPE`, não `%PDF-`; rebaixar os mesmos
    IDs devolveu PDF válido em 5/5 — ou seja, falha **intermitente** (rate-limiting).
    Correção: validação da assinatura `%PDF-` no download E ao reaproveitar arquivo de
    disco, com 3 tentativas e backoff; HTML nunca é gravado. Após o fix, a primeira
    leva processou 91 documentos com **zero falhas** (antes: 100% de falha).
    Lição registrada para o relatório: "HTTP 200" não é sinônimo de conteúdo válido —
    toda coleta automática precisa validar o formato do que recebeu.

---

## 2026-08-12 — FASE 6 (preços e renda fixa)

30. **Preços:** 29.546 cotações (20 tickers + ^BVSP), fechamento AJUSTADO via yfinance
    (`auto_adjust=True`, já incorpora dividendos e desdobramentos), 2020-12-01 até hoje.
    O início 1 mês antes do backtest dá a janela necessária para a tendência de 3 meses.
    Sanidade conferida: PETR4 +481% no período (dividendos extraordinários reinvestidos),
    ^BVSP +50,4%, ITUB4 +106% — coerentes com o período.

31. **Selic:** 1.431 dias (SGS série 11). O BCB publica em PERCENTUAL ao dia; gravamos
    como **fração decimal** (0,052531% → 0,00052531) para acúmulo direto no backtest.
    Média do período: 0,00042208/dia ≈ **11,22% a.a. equivalente**. Os 56 dias úteis sem
    taxa são feriados bancários (~10/ano — esperado). A MESMA série alimenta a perna RF
    do robô, o benchmark 60/40 e a taxa livre de risco do Sharpe (exigência da seção 8).

32. **Buracos nas séries (limitação declarada):** 8 dos 20 tickers não têm cotação em
    2026-07-20 e 2026-07-31 (2 pregões de 1.419 = 0,14%), ausentes na fonte — o ^BVSP
    tem esses dias. Tratamento na FASE 8: repetir o último preço disponível
    (forward-fill), padrão de mercado para falha pontual de fonte. IGTI11 tem 1.180
    pregões (estreia em 2021-11-22, conforme aprovado).

---

## 2026-08-12 — FASE 5 (Google News)

33. **Coleta local via RSS em vez do actor Apify** (mesmo motivo da FASE 3 — aprovação
    manual de permissões por conta). Descoberta que mudou o desenho: o RSS **aceita os
    operadores `after:`/`before:`**, então a coleta é feita EMPRESA × MÊS e preenche o
    histórico inteiro (2021-01 a 2026-06), não só notícias recentes. Custo R$ 0.
    `ticker_alvo` fica NULL de propósito: quem desambigua é a entidade do LLM (regra
    travada); a empresa buscada é registrada no log local de auditoria.
    Dedup em camadas: url única (constraint) + (título normalizado, veículo) — cópia
    idêntica é descartada, mesmo evento em veículos diferentes conta como distinto.

34. **Calibragem das queries após teste-piloto (armadilhas da seção 4 confirmadas na
    prática):** a query ampla trouxe **4.997 itens para B3SA3** (~76/mês) contra 396 da
    ALUP11 — a inspeção mostrou que a maioria não era notícia SOBRE a B3, e sim notícia
    que cita a B3 como BOLSA ("Smart Fit estreia na B3", "ações negociadas na B3") ou
    conteúdo educativo do portal da própria B3. Três defesas adotadas:
    - **queries específicas** para os 6 nomes ambíguos (B3SA3, MOTV3/CCR, ITUB4 vs
      ITSA4, LREN3, ISAE4/CTEEP) usando ticker + razão social em vez do apelido curto;
    - **bloqueio de portais institucionais** (borainvestir.b3.com.br etc.): conteúdo de
      marketing não mede percepção de mercado;
    - **teto de 25 notícias por empresa/mês**: a elegibilidade exige ≥10, então 25 dá
      margem confortável; sem teto, uma empresa sozinha consumiria a cota do Gemini
      (gargalo real do projeto) com ruído que o LLM descartaria depois.
    O lote não calibrado (5.884 notícias) foi apagado e a coleta reiniciada do zero.
    Registrado como decisão metodológica: a precisão da coleta serve à economia de
    cota; a correção do sinal continua vindo da desambiguação por LLM.

35. **Pré-filtro de risco por código (FASE 4) — decisão metodológica.** Medição: enviar
    os 5.065 documentos ao LLM custaria **8,3M tokens**, inviável na cota gratuita
    (chave 1 em 429/cota, chave 2 inválida, chave 3 em 503/sobrecarga). Um documento que
    não cita NENHUM termo ligado às 5 categorias de risco grave não pode ser um veto,
    então é descartado por CÓDIGO antes do LLM. Resultado: **3.319 → 504 documentos
    (−85%)**. Salvaguardas:
    - lista de ~45 termos deliberadamente GENEROSA (prioriza recall sobre precisão);
    - **validada contra o veto conhecido**: o caso MOTV3/RodoNorte passa no filtro
      (casa "tribunal de contas") — se não passasse, a lista seria refeita;
    - status próprio `sem_indicio_risco` (migração aplicada) em vez de 'classificado':
      a auditoria distingue "o LLM analisou e não achou" de "nem foi ao LLM";
    - o código NUNCA decide que algo É veto; só decide o que claramente não é nada.
      O LLM continua sendo o único juiz do risco grave (regra travada preservada).

---

## 2026-08-13 — FASE 5 concluída (cobertura)

36. **Coleta Google News finalizada:** 22.137 notícias novas, 1.654 cópias descartadas
    pela dedup, 1.320 requisições, 0 falhas, **custo R$ 0**. Corpus total de mídia:
    **35.592 notícias** (13.455 GDELT + 22.137 Google News).

37. **Cobertura provisória** (`outputs/cobertura_provisoria.csv`): 951 de 1.320 pares
    empresa-mês passam da regra de ≥10 notícias (72%). Método declarado como PROVISÓRIO:
    usa a empresa buscada (Google News) e casamento de apelido por palavra inteira
    (GDELT); a atribuição definitiva vem da entidade do LLM na FASE 7.
    - Sempre elegíveis (100% dos meses): PETR4, ITUB4, BPAC11, FLRY3, IGTI11, POMO4, SBSP3.
    - Cronicamente abaixo do limiar: TAEE11 (21%), ALUP11 (23%), ISAE4 (26%), GRND3 (32%)
      — small caps de utilities/calçados com pouca cobertura de mídia. NÃO devem sair do
      universo: a regra de elegibilidade já as exclui automaticamente nos meses fracos,
      que é exatamente o comportamento desejado (sem notícia, sem sinal).

38. **Viés de cobertura crescente (limitação declarada para o relatório):** o número de
    empresas elegíveis cresce de ~11/20 em 2021 para 20/20 em 2026, porque a indexação
    do Google News é mais densa para conteúdo recente. Efeito prático: a carteira tende
    a ser mais concentrada no início do backtest e mais diversificada no fim. Não é
    look-ahead (nenhum dado futuro entra no sinal do mês), mas muda o regime da
    estratégia ao longo do tempo e precisa ser dito no relatório.

39. **REVERSÃO da mudança do item 20 — aprovada pelo humano em 2026-08-13.** O período do
    backtest volta ao ORIGINAL: **sinais de jan/2021 a jun/2026, primeira carteira em
    fev/2021**. Justificativa empírica: a coleta do Google News cobriu jan–jun/2021 com
    média de **11,7 empresas elegíveis/mês**, praticamente idêntica aos 12,2 de
    jul–dez/2021 (período já coberto pelo GDELT). A limitação declarada no item 20
    deixa de existir; ganhamos 7 meses de backtest. `config.BACKTEST_INICIO='2021-01'`.

40. **Empresas cronicamente abaixo do limiar (TAEE11 21%, ALUP11 23%, ISAE4 26%,
    GRND3 32%) permanecem no universo.** Removê-las agora seria decidir com base em
    informação de hoje sobre todo o período — viés de seleção. A regra de elegibilidade
    já as exclui automaticamente nos meses sem cobertura, que é o comportamento correto:
    sem notícia, sem sinal. Registrado para o relatório.

41. **Auditoria adversarial do pré-filtro (item 35) — APROVADO.** 23 agentes varreram os
    2.815 documentos descartados por 3 estratégias independentes (varredura de todos os
    assuntos; leitura ampla dos Fatos Relevantes; busca por vocabulário ALTERNATIVO que
    comunicaria risco grave sem usar os termos da lista). Levantaram 20 suspeitas;
    **nenhuma se confirmou** sob verificação. Conclusão registrada: o pré-filtro não
    custou recall de vetos.

---

## 2026-08-13 — FASE 7 (piloto de sentimento)

42. **Piloto de 200 manchetes (150 classificadas antes da cota):** distribuição
    **neutro 38,7% / positivo 33,3% / negativo 28,0%** — saudável, sem degeneração
    (um classificador quebrado tende a colapsar em uma classe só). Mapeamento
    entidade→ticker validado nas armadilhas do plano: "Itaú"→ITUB4 vs "Itaúsa"→ITSA4,
    "CCR"/"Motiva"→MOTV3, e empresas fora do universo (Vale, Magazine Luiza)→NULL.

43. **PONTO DE ATENÇÃO — taxa de mapeamento de 24,7% no piloto.** Só 37 das 150
    manchetes mapearam para empresa do universo. Causa provável: o piloto pegou as
    notícias mais ANTIGAS por id, que são do GDELT e majoritariamente macro
    (combustíveis, Ibovespa, política) — o GDELT foi coletado por tema, não por empresa.
    As do Google News foram buscadas POR empresa e devem mapear muito mais. Se a taxa
    final ficar baixa, a contagem por empresa/mês cai e a elegibilidade fica mais
    restritiva que o previsto na cobertura provisória (item 37), que usava casamento
    ingênuo por apelido. **A medir quando a classificação avançar** — pode exigir
    reavaliar o limiar de 10 notícias/mês (regra travada: só muda com aprovação).
---

## 2026-08-13 — FASE 8 (motor do backtest)

44. **Bug encontrado na 1ª execução: dinheiro parado.** Quando nenhuma empresa é
    elegível no mês (comum onde a cobertura é baixa), a parcela de RV ficava SEM
    destino, rendendo zero. Correção: a RV não investida vai para o Tesouro Selic —
    `peso_rf = 1 − Σ(pesos efetivamente alocados)`, que é o que um gestor faria de fato.
    Sem isso a estratégia seria injustamente penalizada nos meses de baixa cobertura.
    A carteira grava `pct_rv_efetivo` (alocação real) além do `pct_rv` teórico da
    fórmula; o gráfico de alocação e o turnover usam o efetivo.

45. **Teste anti-look-ahead implementado e PASSANDO** (assert que roda como parte do
    backtest): verifica que toda notícia contada no sinal do mês M tem registro no
    próprio mês M, e que a execução cai sempre no 1º pregão de M+1.

46. **Cota do Gemini quantificada (2026-08-13):** o erro 429 informa o limite exato —
    `GenerateRequestsPerDayPerProjectPerModel-FreeTier = **500 requisições/dia**` por
    projeto/modelo. Consequências para o planejamento:
    - o **veto gasta 1 requisição por documento** (documento longo, análise individual
      com citação literal — não dá para lotear sem perder qualidade): 338 restantes = 338 req;
    - a **classificação de sentimento gasta 1 requisição por 25 manchetes**: 35.072
      restantes ≈ 1.403 req;
    - total restante ≈ **1.741 requisições**; capacidade = 500/dia × nº de chaves válidas.
    Com as 2 chaves válidas de hoje: ~2 dias. **Cada chave nova adiciona 500 req/dia
    (= 12.500 manchetes/dia)**, então o prazo cai proporcionalmente.
    Registro honesto: o consumo de hoje foi dominado pelo veto (1.912 requisições), o
    que explica a classificação ter avançado só 400 manchetes.

47. **Suíte de testes do motor do backtest** (`tests/test_backtest.py`, 35 verificações com
    dados sintéticos de resposta conhecida — roda sem banco, rede ou LLM). Cobre: fórmula
    da alocação macro nos 10 casos-limite de S e o clamp [40,80]; pesos ∝ (N+1−rank) com
    proporção exata (N=20 → maior peso 20/210); teto de 15% com renormalização; veto
    sobrepondo o sentimento; elegibilidade em 9 vs 10 notícias; fórmula do S; MERCADO
    incluindo notícias não mapeadas; janela de veto de 6 meses nos 4 pontos críticos;
    métricas contra série de resposta fechada (1,01^12−1, drawdown de −25%); acumulação
    da Selic no intervalo (de, ate]; forward-fill que não enxerga preço futuro; e a
    garantia de que a tendência numérica NÃO altera a decisão.

48. **Defeito encontrado pelos próprios testes: empate de sentimento.** Duas empresas com
    o MESMO S recebiam pesos muito diferentes (66,7% vs 33,3%) porque o desempate saía da
    ordem de leitura do banco — arbitrário e não replicável. A regra travada não define
    tratamento de empate. **Decisão registrada:** empresas com S idêntico dividem a MÉDIA
    dos pesos do bloco empatado (tratamento padrão de empates em ranking), e a ordenação
    ganhou desempate determinístico por ticker. Caso relevante na prática: vários S = 0,0
    no mesmo mês. Não altera a regra — preenche uma lacuna dela.

49. **Modelo secundário para veto e cartas — APROVADO pelo humano em 2026-08-13.**
    Descoberta: a cota gratuita é `GenerateRequestsPerDayPerProjectPerModel` = 500/dia
    **POR MODELO**, então um segundo modelo pinado dobra a capacidade na mesma chave.
    Decisão híbrida: o **sentimento continua 100% no modelo pinado principal**
    (`gemini-3.1-flash-lite`), porque é ele que gera o sinal S de todo o backtest e
    misturar modelos criaria heterogeneidade no sinal. Veto e cartas passam a usar
    `GEMINI_MODEL_SECUNDARIO` (`gemini-3.5-flash-lite`): o veto é binário e tem citação
    **validada por código** (robusto a troca de modelo) e as cartas são narrativa que não
    entra em cálculo nenhum. Cada linha grava o modelo que a produziu. O cliente recusa
    aliases `-latest` também no secundário.

50. **BUG GRAVE (auditoria): teto de 15% ignorado com 6 ou menos empresas.** O laço saía
    por `if not livres: break` ANTES de aplicar `min(p, TETO)`. Materializado em dados
    reais: out/2021 e jun/2022 tinham PETR4 com **peso 1,0 = 100% da renda variável**
    (6,7× o teto). Pior: para N entre 4 e 6 o excedente era despejado na posição "livre",
    **invertendo a ordenação** — a ação de sentimento MAIS POSITIVO (a que a estratégia
    contrária menos quer) ficava com 55% e a de sentimento mais negativo com 15%.
    Corrigido: o corte vem antes de qualquer saída do laço. Com N ≤ 6 o teto trava todas
    e a soma fica < 1 de propósito — a sobra da RV vai para o Tesouro Selic (item 44).
    **Meus próprios testes não pegaram porque eu havia codificado o comportamento errado
    como se fosse a regra** ("com 1 elegível ela leva 100% da RV"). Teste corrigido e
    ampliado para varrer N = 1..20 verificando teto e monotonia.

51. **BUG GRAVE (auditoria): série do robô desalinhada dos benchmarks em 1 mês.** O ponto
    gravado na data d_i já continha o retorno do período (d_i, d_i+1], enquanto os
    benchmarks gravavam o valor correto em cada data — as duas curvas mediam janelas
    diferentes e a comparação era inválida. Efeito medido: robô 100% Selic aparecia
    1,22% ACIMA do próprio benchmark Selic, e o Sharpe dava 0,52 onde o correto é 0.
    Corrigido: a série passa a começar em 1,00 na primeira data e cada ponto contém tudo
    que aconteceu ATÉ aquela data — mesma convenção de `benchmarks()`. Teste de regressão:
    robô 100% Selic tem de bater EXATAMENTE o benchmark Selic (com custo zerado).

52. **Turnover agora considera o drift de preços.** Comparava o alvo do mês com o alvo do
    mês anterior, ignorando que os pesos derivam com a variação dos preços — subestimava
    o volume realmente negociado. Passa a comparar com a carteira efetivamente carregada.

53. **Retorno ano a ano creditado ao ano errado.** O retorno do período que começa em
    01/12/2021 e termina em 03/01/2022 é retorno de DEZEMBRO/2021, mas era lançado em
    2022. Corrigido para creditar ao ano do início do período.

54. **BUG CRÍTICO DE AUDITORIA: o teste anti-look-ahead era tautológico.** Suas duas
    asserções eram verdadeiras por construção — o assert nunca poderia disparar. Ou seja,
    a garantia "teste anti-look-ahead passando" que eu havia reportado **não provava nada**.
    Corrigido em duas partes: (a) recontagem INDEPENDENTE das notícias do próprio mês,
    comparada com o `n_noticias` usado em cada carteira; (b) **teste de MUTAÇÃO** — o
    backtest injeta de propósito um look-ahead (desloca as notícias um mês) e EXIGE que a
    checagem acuse. Resultado atual: 74 violações detectadas na mutação, provando que o
    teste tem poder de detecção. Sem (b), qualquer teste desse tipo pode ser vazio.

55. **Cartas misturavam bases percentuais.** O JSON enviado ao LLM trazia o peso como
    fatia da RENDA VARIÁVEL ao lado de `pct_bolsa`, que é fatia do PATRIMÔNIO — a carta
    publicava posições maiores que as reais. Agora o peso vai na mesma base
    (`peso_pct_do_patrimonio`).

56. **Teste de lote 25 vs 50 manchetes (aprovado pelo humano fazer o teste):** em 100
    manchetes idênticas, a concordância de **sentimento foi 88%** e a de **entidade 64%**.
    Abaixo do critério de 95% que eu havia definido para adotar 50. **Decisão: manter 25**,
    conforme o plano original. Observação relevante para o relatório: a divergência de 12%
    entre dois tamanhos de lote do MESMO modelo é uma medida honesta da instabilidade
    intrínseca do classificador — vale citar como limitação e reforça a necessidade do
    gabarito humano para medir acurácia.

---

## 2026-08-16 — Relatório final (AAFQ.pdf)

57. **Classificação de sentimento CONCLUÍDA: 35.472 de 35.472 manchetes (100%).**
    Paralelização em 3 shards disjuntos (id % 3), cada um com chave própria das 3 novas
    fornecidas pela equipe. Distribuição final: 38,2% positivas, 19,5% negativas, 42,3%
    neutras; 60,4% mapeadas para empresas do universo. Achado relevante: o S do mercado
    foi positivo em 65 dos 66 meses (neutro em mar/2024) — o noticiário financeiro é
    estruturalmente otimista, e o sinal do KRON operou nos degraus de intensidade do
    otimismo (%Bolsa teórico entre 51,6% e 60,0%, sem tocar os clamps de 40/80).

58. **Resultados finais do backtest (100% do corpus):** KRON contrária 122,2% (15,9%
    a.a., Sharpe 0,40, maxDD −9,7%, turnover 17,8%/mês); invertida 105,5% (Sharpe 0,25);
    Ibovespa 46,3%; Selic 79,5%; 60/40 62,0%. A contrária vence os 3 benchmarks e o
    contrateste. Teste anti-look-ahead passou; mutação detectada com 1.198 violações.

59. **Robô renomeado: Hermes → KRON** (Kernel de Rastreamento de Otimismo e Notícias),
    nome e identidade definidos pela equipe. As 2 cartas antigas do "Hermes" no banco
    ficam obsoletas (cartas não integram o relatório final).

60. **Relatório final gerado (relatorio/AAFQ.pdf):** 5 páginas 16:9, anônimo, identidade
    visual do KRON (paleta sol/lua, anel-relógio, Bahnschrift), texto varrido pelas
    regras anti-IA da equipe. Pipeline reproduzível: 08_backtest → gerar_graficos →
    construir (números injetados de metricas.json; nada digitado à mão).
    **Auditoria final multi-agente: 17 achados confirmados, todos corrigidos** — número
    desatualizado do teste de mutação (74→1.198), "todos os 66 meses" corrigido para
    "65 dos 66" (mar/2024 foi neutro, o próprio gráfico mostrava), "39 verificações"
    corrigido para 58 (suíte atual; DECISOES item 47 registrava 35 de antes da
    ampliação), escopo dos 14 defeitos restrito ao motor, ponte 5.102 triados → 2.250
    lidos pelo LLM, vol. da Selic exibida (1,0%), divergência de entidade (64%)
    declarada junto com a de sentimento (12%), "decisão cautelar" do TCE-PR explicitada,
    ortografia AO90 (contraindicador/contrateste). Nenhuma violação eliminatória
    (formato, anonimato e idioma conferidos contra o edital).

## Pendências abertas
- [x] DDL no Supabase — resolvido em 2026-07-19 via MCP (item 14).
- [x] Data-base das métricas do CSV — respondido pelo humano em 2026-07-19 (item 17).
- [x] `NOME_DO_ROBO` — Hermes (item 13).
- [x] GitHub — resolvido em 2026-07-19: login via device flow (conta Bielleon),
      repositório PRIVADO criado em https://github.com/Bielleon/QuantAI, push ok.
- [ ] SUPABASE_ANON_KEY inválida (401) — recopiar do painel quando formos fazer dashboard.
- [ ] Chave Gemini 2/3 inválida (401) — regenerar no AI Studio/console.
