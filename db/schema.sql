-- ============================================================
-- QuantAI — Schema do banco (Supabase / Postgres)
-- Idempotente: pode ser executado mais de uma vez sem erro.
-- ============================================================

-- 1. UNIVERSO INVESTÍVEL (importado de "Analise de acoes confiaveis.csv")
create table if not exists universo (
  ticker              text primary key,
  nome                text not null,
  setor               text,
  apelidos            text,            -- nomes populares p/ busca de notícias, separados por ';'
  metricas_qualidade  jsonb,           -- métricas do filtro de qualidade da equipe (justificativa do universo)
  ativo               boolean not null default true
);

-- 2. NOTÍCIAS DE MÍDIA (GDELT histórico + Google News recente) — fonte de SENTIMENTO
create table if not exists noticias (
  id          bigint generated always as identity primary key,
  url         text not null unique,
  titulo      text,
  fonte       text not null check (fonte in ('gdelt','google_news')),
  veiculo     text,
  data_pub    timestamptz,
  tom_gdelt   double precision,        -- tom original do GDELT, quando existir
  ticker_alvo text references universo(ticker),  -- preenchido após desambiguação via LLM
  criado_em   timestamptz not null default now()
);
create index if not exists idx_noticias_data_pub on noticias (data_pub);
create index if not exists idx_noticias_ticker on noticias (ticker_alvo);

-- 3. SENTIMENTOS (classificação LLM, 1-para-1 com notícia)
create table if not exists sentimentos (
  id             bigint generated always as identity primary key,
  noticia_id     bigint not null unique references noticias(id) on delete cascade,
  sentimento     text not null check (sentimento in ('positivo','negativo','neutro')),
  entidade_llm   text,                 -- entidade devolvida pelo LLM (desambiguação)
  ticker_mapeado text references universo(ticker),
  modelo         text,                 -- nome do modelo usado (pinado)
  model_version  text,                 -- modelVersion devolvido pela API
  prompt_hash    text,                 -- sha256 do prompt congelado
  criado_em      timestamptz not null default now()
);
create index if not exists idx_sentimentos_ticker on sentimentos (ticker_mapeado);

-- 4. DOCUMENTOS CVM (IPE: fatos relevantes + comunicados) — fonte EXCLUSIVA de VETO
create table if not exists documentos_cvm (
  id                   bigint generated always as identity primary key,
  ticker               text references universo(ticker),
  categoria            text,
  tipo                 text,
  assunto              text,
  data_entrega         date,
  link_download        text not null unique,
  texto_extraido       text,
  status_processamento text not null default 'pendente'
    check (status_processamento in ('pendente','extraido','vazio_ou_escaneado','erro_download','erro_extracao','classificado'))
);
create index if not exists idx_doccvm_ticker on documentos_cvm (ticker);

-- 5. FLAGS DE RISCO GRAVE (saída do extrator de veto, com validação literal)
-- Auditoria LLM: mesmo trio modelo/model_version/prompt_hash da tabela sentimentos,
-- porque o veto também é uma saída de LLM (prompt congelado 9.2) e manda mais que o sentimento.
create table if not exists flags_relatorio (
  id                bigint generated always as identity primary key,
  ticker            text references universo(ticker),
  doc_id            bigint references documentos_cvm(id) on delete cascade,
  tipo_risco        text,
  citacao_literal   text,
  validacao_literal boolean,           -- true = citação confirmada por código como substring do texto
  mes_evento        date,              -- 1º dia do mês do evento
  veto_de           date,
  veto_ate          date,
  modelo            text,
  model_version     text,
  prompt_hash       text,
  unique (doc_id)                      -- 1 classificação de veto por documento (idempotência)
);
create index if not exists idx_flags_ticker on flags_relatorio (ticker);

-- 6. PREÇOS AJUSTADOS (tickers .SA + ^BVSP; ^BVSP não referencia universo)
create table if not exists precos (
  ticker        text not null,
  data          date not null,
  fech_ajustado double precision not null,
  primary key (ticker, data)
);

-- 7. TAXA LIVRE DE RISCO (Selic diária, série SGS 11 do BCB)
create table if not exists taxa_rf (
  data        date primary key,
  taxa_diaria double precision not null,
  fonte       text not null default 'selic'
);

-- 8. INDICADORES MENSAIS (por empresa e escopo 'MERCADO')
create table if not exists indicadores (
  escopo              text not null,   -- 'MERCADO' ou ticker
  mes                 date not null,   -- 1º dia do mês
  n_noticias          integer,
  n_pos               integer,
  n_neg               integer,
  n_neu               integer,
  s                   double precision, -- (n_pos - n_neg) / (n_pos + n_neg + n_neu)
  tendencia_numerica  double precision, -- retorno acumulado ~63 pregões (auditoria)
  vetado              boolean,
  elegivel            boolean,
  primary key (escopo, mes)
);

-- 9. CARTEIRAS MENSAIS (2 variantes; posicoes = jsonb com variáveis SEPARADAS por auditoria)
create table if not exists carteiras (
  mes       date not null,
  variante  text not null check (variante in ('contraria','invertida')),
  pct_rv    double precision,
  s_mercado double precision,
  posicoes  jsonb,
  criado_em timestamptz not null default now(),
  primary key (mes, variante)
);

-- 10. CARTAS MENSAIS DO ROBÔ
create table if not exists cartas (
  mes      date not null,
  variante text not null check (variante in ('contraria','invertida')),
  texto    text,
  primary key (mes, variante)
);

-- ============================================================
-- Segurança: RLS ligado em tudo, SEM policies.
-- Efeito: a chave anon não lê nada (reservada p/ futuro dashboard,
-- quando criaremos policies de leitura); a service_role (backend)
-- ignora RLS por design do Supabase.
-- ============================================================
alter table universo         enable row level security;
alter table noticias         enable row level security;
alter table sentimentos      enable row level security;
alter table documentos_cvm   enable row level security;
alter table flags_relatorio  enable row level security;
alter table precos           enable row level security;
alter table taxa_rf          enable row level security;
alter table indicadores      enable row level security;
alter table carteiras        enable row level security;
alter table cartas           enable row level security;
