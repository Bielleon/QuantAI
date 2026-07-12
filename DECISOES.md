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

## Pendências abertas
- [ ] DDL no Supabase (decisão do humano: SQL Editor ou connection string).
- [ ] Data-base das métricas do CSV do universo (viés de sobrevivência — registrar como foi feito o filtro).
- [ ] `NOME_DO_ROBO` para as cartas mensais (seção 9.3) — não definido no plano.
- [ ] Autenticação do GitHub CLI (`gh auth login`) para criar o repositório privado.
- [ ] SUPABASE_ANON_KEY inválida (401) — recopiar do painel quando formos fazer dashboard.
- [ ] Chave Gemini 2/3 inválida (401) — regenerar no AI Studio/console.
