"""FASE 6 — Preços ajustados e taxa livre de risco.

precos : fechamento AJUSTADO (yfinance auto_adjust=True — já incorpora
         dividendos e desdobramentos) de 2020-12-01 até hoje, para todos os
         tickers ativos do universo + ^BVSP (benchmark Ibovespa).
         Emendas CCRO3→MOTV3 / TRPL4→ISAE4 se mostraram DESNECESSÁRIAS na
         FASE 1 (o yfinance preserva o histórico sob o ticker novo).
         IGTI11 só existe a partir de 2021-11-22 (aprovado: inelegível antes).
taxa_rf: Selic diária da API SGS do Banco Central (série 11). O valor vem em
         PERCENTUAL ao dia (ex.: 0,052531 = 0,052531%/dia); guardamos como
         fração decimal (0,00052531) para uso direto no acúmulo do backtest.
         A MESMA série alimenta a perna RF do robô, o benchmark 60/40 e a taxa
         livre de risco do Sharpe (consistência exigida pela seção 8).

Verificação de buracos: compara os pregões de cada ticker com o calendário do
^BVSP (referência da B3) e reporta ausências; para taxa_rf, checa dias úteis
sem taxa. Idempotente: upsert por (ticker,data) e (data).
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

INICIO = "2020-12-01"   # 1 mês antes do backtest: janela para a tendência de 3 meses
URL_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados"
LOTE = 1000


def baixar_precos(tickers: list[str]) -> pd.DataFrame:
    """Devolve DataFrame [ticker, data, fech_ajustado] com os fechamentos ajustados."""
    import yfinance as yf

    linhas = []
    for ticker in tickers:
        simbolo = ticker if ticker.startswith("^") else f"{ticker}.SA"
        hist = yf.Ticker(simbolo).history(start=INICIO, auto_adjust=True)
        if hist.empty:
            print(f"  [XX] {ticker}: série VAZIA — investigar antes de seguir")
            continue
        serie = hist["Close"].dropna()
        for dt, valor in serie.items():
            linhas.append({"ticker": ticker, "data": dt.date().isoformat(),
                           "fech_ajustado": round(float(valor), 6)})
        print(f"  [OK] {ticker:<8} {serie.index[0].date()} -> {serie.index[-1].date()} "
              f"({len(serie)} pregões)")
    return pd.DataFrame(linhas)


def baixar_selic() -> pd.DataFrame:
    """Selic diária (SGS 11), convertida de percentual para fração decimal."""
    hoje = date.today().strftime("%d/%m/%Y")
    r = requests.get(URL_SGS, params={"formato": "json", "dataInicial": "01/12/2020",
                                      "dataFinal": hoje}, timeout=120)
    r.raise_for_status()
    linhas = [{"data": datetime.strptime(d["data"], "%d/%m/%Y").date().isoformat(),
               "taxa_diaria": float(d["valor"]) / 100.0,  # percentual -> fração
               "fonte": "selic"}
              for d in r.json()]
    return pd.DataFrame(linhas)


def gravar(tabela: str, df: pd.DataFrame, on_conflict: str) -> None:
    registros = df.to_dict("records")
    for i in range(0, len(registros), LOTE):
        db.upsert(tabela, registros[i:i + LOTE], on_conflict=on_conflict)
        print(f"    {min(i + LOTE, len(registros))}/{len(registros)} enviados", end="\r")
    print()


def verificar_buracos(precos: pd.DataFrame, selic: pd.DataFrame) -> None:
    """Compara cada ticker com o calendário do ^BVSP e reporta ausências."""
    print("\n=== Verificação de buracos ===")
    calendario = set(precos.loc[precos["ticker"] == "^BVSP", "data"])
    print(f"Calendário de referência (^BVSP): {len(calendario)} pregões")

    for ticker in sorted(precos["ticker"].unique()):
        if ticker == "^BVSP":
            continue
        datas = set(precos.loc[precos["ticker"] == ticker, "data"])
        inicio_ticker = min(datas)
        # só cobra pregões a partir da estreia do ticker (IGTI11 estreia em nov/2021)
        esperados = {d for d in calendario if d >= inicio_ticker}
        faltando = sorted(esperados - datas)
        aviso = "" if not faltando else f"  << {len(faltando)} faltando (ex.: {faltando[:3]})"
        marca = "OK " if len(faltando) <= 3 else "!! "
        print(f"  [{marca}] {ticker:<8} {len(datas)} pregões desde {inicio_ticker}{aviso}")

    dias_uteis = {d.date().isoformat() for d in pd.bdate_range(min(selic["data"]), max(selic["data"]))}
    sem_taxa = sorted(dias_uteis - set(selic["data"]))
    print(f"\n  taxa_rf: {len(selic)} dias com taxa | {len(sem_taxa)} dias úteis sem taxa "
          f"(feriados bancários: esperado ~10/ano)")
    if sem_taxa:
        print(f"    exemplos: {sem_taxa[:5]}")


def main():
    empresas = db.selecionar("universo", {"ativo": "eq.true", "select": "ticker"}, order="ticker")
    tickers = [e["ticker"] for e in empresas] + ["^BVSP"]
    print(f"=== Preços ajustados ({len(tickers)} séries, desde {INICIO}) ===")
    precos = baixar_precos(tickers)
    print(f"\nGravando {len(precos)} cotações...")
    gravar("precos", precos, on_conflict="ticker,data")

    print("\n=== Selic diária (BCB SGS série 11) ===")
    selic = baixar_selic()
    print(f"  {len(selic)} dias: {selic['data'].min()} -> {selic['data'].max()}")
    print(f"  taxa média diária: {selic['taxa_diaria'].mean():.8f} "
          f"(~{((1 + selic['taxa_diaria'].mean()) ** 252 - 1) * 100:.2f}% a.a. equivalente)")
    gravar("taxa_rf", selic, on_conflict="data")

    verificar_buracos(precos, selic)

    print(f"\nBanco: precos={db.contar('precos')} | taxa_rf={db.contar('taxa_rf')}")


if __name__ == "__main__":
    main()
