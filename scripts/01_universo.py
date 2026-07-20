"""FASE 1 — Universo investível.

Importa 'Analise de acoes confiaveis (1).csv' para a tabela `universo`:
- descarta linhas órfãs (sem ticker, só com volume) e colunas 100% vazias
  (nivel_governanca, aprovado_final, observacoes) — registrado no DECISOES.md;
- métricas de qualidade guardadas em jsonb (justificativa quantitativa do universo);
- enriquece com a coluna `apelidos` (nomes populares p/ busca de notícias);
- desativa (ativo=false) a classe MENOS líquida de empresas duplicadas
  (PETR3/ITUB3): 1 empresa = 1 ticker, evita dupla contagem de notícias;
- valida cada ticker ativo no yfinance (existência de série desde 2020-12;
  MOTV3/ISAE4 com emenda CCRO3/TRPL4 se a série for curta — aplicada na FASE 6).
Idempotente: upsert por ticker.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

ARQUIVO_CSV = config.RAIZ / "Analise de acoes confiaveis (1).csv"

# Apelidos definidos pela equipe (prompt mestre, seção 4): nomes que a mídia usa.
APELIDOS = {
    "WEGE3": "weg",
    "IGTI11": "iguatemi",
    "MULT3": "multiplan",
    "GRND3": "grendene",
    "LREN3": "renner;lojas renner",
    "HYPE3": "hypera;hypera pharma",
    "EGIE3": "engie;engie brasil",
    "ISAE4": "isa energia;isa cteep;cteep",
    "PETR4": "petrobras;petrobrás",
    "TAEE11": "taesa;transmissora alianca",
    "ITSA4": "itausa;itaúsa",
    "MOTV3": "motiva;ccr;grupo ccr",
    "POMO4": "marcopolo",
    "SBSP3": "sabesp",
    "FLRY3": "fleury;grupo fleury",
    "B3SA3": "b3;bolsa b3",
    "BPAC11": "btg;btg pactual",
    "ITUB4": "itau;itaú;itau unibanco",
    "TOTS3": "totvs",
    "ALUP11": "alupar",
}

# Classe menos líquida de empresa que aparece 2x no CSV -> fica fora (ativo=false).
CLASSES_DESATIVADAS = {
    "PETR3": "classe menos líquida da Petrobras (volume 9,7M vs 27,3M da PETR4); 1 empresa = 1 ticker",
    "ITUB3": "classe menos líquida do Itaú (volume 1,8M vs 28,8M da ITUB4); 1 empresa = 1 ticker",
}

# Setores em inglês (resquício da fonte de dados da equipe) -> padrão pt-BR do CSV.
SETOR_NORMALIZADO = {
    "Finance": "Serviços Financeiros",
    "Producer Manufacturing": "Bens Industriais",
    "Utilities": "Energia",
}

# Renomeações conhecidas no período (emenda de preços feita na FASE 6)
TICKERS_ANTIGOS = {"MOTV3": "CCRO3", "ISAE4": "TRPL4"}


def carregar_csv() -> tuple[list[dict], list[dict]]:
    """Lê o CSV; devolve (linhas válidas, linhas órfãs descartadas).

    utf-8-sig: tolera BOM que o Excel adiciona ao salvar como 'CSV UTF-8'
    (sem isso o 1º cabeçalho viraria '\\ufeffticker' e todas as linhas
    seriam descartadas em silêncio).
    """
    validas, orfas = [], []
    with ARQUIVO_CSV.open(encoding="utf-8-sig") as f:
        leitor = csv.DictReader(f)
        if "ticker" not in (leitor.fieldnames or []):
            raise SystemExit(f"Cabeçalho inesperado no CSV: {leitor.fieldnames} — coluna 'ticker' não encontrada.")
        for linha in leitor:
            if (linha.get("ticker") or "").strip():
                validas.append(linha)
            else:
                orfas.append(linha)
    if not validas:
        raise SystemExit("Nenhuma linha válida no CSV — arquivo corrompido/reexportado? Abortando sem tocar no banco.")
    return validas, orfas


def montar_registro(linha: dict) -> dict:
    ticker = linha["ticker"].strip()
    setor_original = linha["setor"].strip()
    metricas = {
        "anos_disponiveis": float(linha["anos_disponiveis"]),
        "anos_com_prejuizo": float(linha["anos_com_prejuizo"]),
        "roe_medio_pct": float(linha["roe_medio_%"]),
        "roe_desvio_padrao_pct": float(linha["roe_desvio_padrao_%"]),  # zerado no CSV — limitação declarada
        "debt_to_equity": float(linha["debt_to_equity"]),
        "margem_liquida_pct": float(linha["margem_liquida_%"]),
        "percentil_margem_setor_pct": float(linha["percentil_margem_setor_%"]),
        "volume_medio": float(linha["volume"]),
    }
    if setor_original in SETOR_NORMALIZADO:
        metricas["setor_original"] = setor_original
    if ticker in CLASSES_DESATIVADAS:
        metricas["motivo_inativo"] = CLASSES_DESATIVADAS[ticker]
    return {
        "ticker": ticker,
        "nome": linha["nome"].strip(),
        "setor": SETOR_NORMALIZADO.get(setor_original, setor_original),
        "apelidos": APELIDOS.get(ticker),
        "metricas_qualidade": metricas,
        "ativo": ticker not in CLASSES_DESATIVADAS,
    }


def validar_yfinance(tickers: list[str]) -> list[dict]:
    """Confere se cada ticker .SA tem série de preços desde ~2020-12 no yfinance."""
    import yfinance as yf

    relatorio = []
    for ticker in tickers + ["^BVSP"]:
        simbolo = ticker if ticker.startswith("^") else f"{ticker}.SA"
        try:
            hist = yf.Ticker(simbolo).history(start="2020-12-01", auto_adjust=True)
            if hist.empty:
                relatorio.append({"ticker": ticker, "ok": False, "detalhe": "série vazia"})
                continue
            inicio, fim = hist.index[0].date(), hist.index[-1].date()
            precisa_emenda = ticker in TICKERS_ANTIGOS and inicio.isoformat() > "2021-01-15"
            detalhe = f"{inicio} -> {fim} ({len(hist)} pregões)"
            if precisa_emenda:
                detalhe += f" | EMENDA NECESSÁRIA com {TICKERS_ANTIGOS[ticker]}.SA"
            relatorio.append({"ticker": ticker, "ok": True, "inicio": str(inicio), "fim": str(fim),
                              "n": len(hist), "emenda": precisa_emenda, "detalhe": detalhe})
        except Exception as e:  # noqa: BLE001
            relatorio.append({"ticker": ticker, "ok": False, "detalhe": config.redigir(str(e))[:120]})
    return relatorio


def main():
    validas, orfas = carregar_csv()
    print(f"CSV: {len(validas)} linhas válidas, {len(orfas)} órfãs descartadas "
          f"(volumes órfãos: {[o.get('volume') for o in orfas]})")

    registros = [montar_registro(l) for l in validas]
    sem_apelido = [r["ticker"] for r in registros if r["ativo"] and not r["apelidos"]]
    if sem_apelido:
        print(f"ATENÇÃO: tickers ativos SEM apelido definido (decidir com o humano): {sem_apelido}")

    n = db.upsert("universo", registros, on_conflict="ticker")
    print(f"Upsert em `universo`: {n} linhas enviadas")

    ativos = sorted(r["ticker"] for r in registros if r["ativo"])
    print(f"\nUniverso ativo ({len(ativos)}): {', '.join(ativos)}")

    print("\nValidação yfinance (série desde 2020-12):")
    problemas = []
    for item in validar_yfinance(ativos):
        status = "OK " if item["ok"] else "XX "
        print(f"  [{status}] {item['ticker']:<8} {item['detalhe']}")
        if not item["ok"] or item.get("emenda"):
            problemas.append(item["ticker"])

    resumo = {
        "no_banco": db.contar("universo"),
        "ativos": len(ativos),
        "orfas_descartadas": len(orfas),
        "problemas_yfinance": problemas,
    }
    print(f"\nResumo: {json.dumps(resumo, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
