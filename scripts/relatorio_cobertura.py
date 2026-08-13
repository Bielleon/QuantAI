"""Relatório de cobertura de notícias por empresa × mês (FASE 5).

ATENÇÃO — ESTIMATIVA PROVISÓRIA. A atribuição DEFINITIVA de qual empresa cada
notícia trata vem da entidade devolvida pelo LLM na FASE 7 (regra travada:
"desambiguação final SEMPRE pela entidade do LLM"). Aqui usamos duas
aproximações, declaradas:
  - google_news: a empresa que foi BUSCADA (log data/gnews_coletados.jsonl);
  - gdelt: casamento do apelido no título por PALAVRA INTEIRA (uma notícia
    que cite duas empresas conta para as duas — o LLM depois escolhe uma só).
Serve para decidir cobertura/elegibilidade antes de gastar cota de LLM.

Saídas: outputs/cobertura_provisoria.csv (empresa × mês) e um resumo no console
com a checagem da regra de elegibilidade (>= 10 notícias de mídia no mês).
"""
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

ARQ_GNEWS = config.DATA_DIR / "gnews_coletados.jsonl"
SAIDA = config.OUTPUTS_DIR / "cobertura_provisoria.csv"
MES_INI, MES_FIM = "2021-01", "2026-06"


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", t)


def meses_do_periodo() -> list[str]:
    saida, ano, mes = [], 2021, 1
    while f"{ano}-{mes:02d}" <= MES_FIM:
        saida.append(f"{ano}-{mes:02d}")
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return saida


def main():
    empresas = db.selecionar("universo", {"ativo": "eq.true", "select": "ticker,apelidos,nome"},
                             order="ticker")
    padroes = {}
    for e in empresas:
        termos = [normalizar(a) for a in (e["apelidos"] or "").split(";") if a.strip()]
        termos.append(e["ticker"].lower())
        # palavra inteira: evita 'b3' casar dentro de 'b3sa3xyz' e 'itau' dentro de 'itausa'
        padroes[e["ticker"]] = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in termos) + r")(?![a-z0-9])")

    contagem: dict[tuple[str, str], int] = defaultdict(int)

    # --- GDELT: casamento por apelido no título
    gdelt = db.selecionar("noticias", {"fonte": "eq.gdelt", "select": "titulo,data_pub",
                                       "titulo": "not.is.null"}, order="id")
    for n in gdelt:
        mes = n["data_pub"][:7]
        titulo = normalizar(n["titulo"])
        for ticker, padrao in padroes.items():
            if padrao.search(titulo):
                contagem[(ticker, mes)] += 1

    # --- Google News: empresa buscada (log de auditoria)
    n_gnews = 0
    if ARQ_GNEWS.exists():
        for linha in ARQ_GNEWS.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(linha)
            except json.JSONDecodeError:
                continue
            contagem[(r["ticker_buscado"], r["mes"][:7])] += 1
            n_gnews += 1

    meses = meses_do_periodo()
    tickers = sorted(padroes)

    config.OUTPUTS_DIR.mkdir(exist_ok=True)
    with SAIDA.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker"] + meses)
        for t in tickers:
            w.writerow([t] + [contagem.get((t, m), 0) for m in meses])

    print(f"GDELT casados por apelido: {sum(1 for n in gdelt)} títulos varridos | "
          f"Google News no log: {n_gnews}")
    print(f"CSV: {SAIDA}\n")

    print("=== Meses ELEGÍVEIS (>= 10 notícias) por empresa ===")
    print(f"{'ticker':<9} {'elegíveis':>10} {'de':>4} {'%':>6}   {'média/mês':>10}")
    total_elegiveis = 0
    for t in tickers:
        valores = [contagem.get((t, m), 0) for m in meses]
        ok = sum(1 for v in valores if v >= config.MIN_NOTICIAS_MES)
        total_elegiveis += ok
        media = sum(valores) / len(valores)
        print(f"{t:<9} {ok:>10} {len(meses):>4} {100*ok/len(meses):>5.0f}%   {media:>10.1f}")
    print(f"\nCobertura geral: {total_elegiveis}/{len(tickers)*len(meses)} pares empresa-mês "
          f"({100*total_elegiveis/(len(tickers)*len(meses)):.0f}%)")

    print("\n=== Elegíveis por mês (quantas empresas passam do limiar) ===")
    for m in meses:
        n = sum(1 for t in tickers if contagem.get((t, m), 0) >= config.MIN_NOTICIAS_MES)
        barra = "#" * n
        marca = "  <-- antes do GDELT" if m < "2021-07" else ""
        print(f"  {m}  {n:>2}/{len(tickers)} {barra}{marca}")


if __name__ == "__main__":
    main()
