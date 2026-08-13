"""Configuração central do QuantAI: carrega o .env e expõe as chaves.

Regra de ouro: NUNCA imprimir chaves inteiras — use mascarar().
"""
import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_ENV = RAIZ / ".env"
load_dotenv(ARQUIVO_ENV)

DATA_DIR = RAIZ / "data"
OUTPUTS_DIR = RAIZ / "outputs"
DATA_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

APIFY_TOKENS = [t.strip() for t in os.environ.get("APIFY_TOKENS", "").split(",") if t.strip()]
GEMINI_API_KEYS = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
GEMINI_MODEL_PINNED = os.environ.get("GEMINI_MODEL_PINNED", "").strip()

# Parâmetros de negócio travados (seção 8 do prompt mestre — mudar SÓ com aprovação registrada)
VETO_MESES = 6
MIN_NOTICIAS_MES = 10
TETO_PESO_ACAO = 0.15            # fração (teto de 15% por ação)
CUSTO_TURNOVER = 0.001           # fração (0,1% sobre o turnover)
PCT_RV_BASE = 60.0               # %RV = base -/+ sensibilidade * S_mercado (escala percentual)
PCT_RV_SENSIBILIDADE = 20.0
PCT_RV_MIN, PCT_RV_MAX = 40.0, 80.0
BACKTEST_INICIO = "2021-01"      # 1º mês de sinal; 1ª carteira executa em fev/2021.
                                 # Voltou ao valor original em 2026-08-13 (aprovado): a
                                 # coleta do Google News cobriu jan–jun/2021 com densidade
                                 # equivalente à do resto de 2021. Ver DECISOES.md item 39.
BACKTEST_FIM = "2026-06"
LLM_TEMPERATURE = 0.0            # todas as chamadas LLM (prompts congelados, seção 9)

# Lista de todos os segredos conhecidos, usada por redigir() (defesa em profundidade)
_SEGREDOS = [s for s in ([SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY]
                         + APIFY_TOKENS + GEMINI_API_KEYS) if s]


def mascarar(segredo: str) -> str:
    """Devolve versão segura de uma chave para logs: 'apify_ap…z42l'."""
    if not segredo:
        return "(vazia)"
    if len(segredo) <= 14:
        return segredo[:2] + "…"
    return segredo[:8] + "…" + segredo[-4:]


def redigir(texto: str) -> str:
    """Substitui qualquer segredo presente no texto pela versão mascarada.

    Mensagens de exceção do requests podem embutir a URL chamada; aplicar
    redigir() em TODO texto de erro antes de imprimir/propagar garante que
    nenhuma chave inteira escape para console, log ou arquivo.
    """
    for segredo in _SEGREDOS:
        if segredo in texto:
            texto = texto.replace(segredo, mascarar(segredo))
    return texto


def gravar_model_pinned(nome_modelo: str) -> None:
    """Grava GEMINI_MODEL_PINNED no .env (replicabilidade: nunca usar alias -latest em produção)."""
    global GEMINI_MODEL_PINNED
    linhas = ARQUIVO_ENV.read_text(encoding="utf-8").splitlines()
    novas = []
    achou = False
    for linha in linhas:
        if linha.startswith("GEMINI_MODEL_PINNED="):
            novas.append(f"GEMINI_MODEL_PINNED={nome_modelo}")
            achou = True
        else:
            novas.append(linha)
    if not achou:
        novas.append(f"GEMINI_MODEL_PINNED={nome_modelo}")
    ARQUIVO_ENV.write_text("\n".join(novas) + "\n", encoding="utf-8")
    GEMINI_MODEL_PINNED = nome_modelo
