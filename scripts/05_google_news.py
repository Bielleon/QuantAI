"""FASE 5 — Notícias do Google News (coleta local, custo R$ 0).

Previsto originalmente via actor Apify Pay-Per-Event; trocado por coleta local
do feed RSS do Google News pelo mesmo motivo da FASE 3 (a Apify passou a exigir
aprovação manual de permissões por conta). Ver DECISOES.md item 33.

O RSS aceita os operadores `after:`/`before:`, então a coleta é feita
EMPRESA × MÊS — o que preenche o histórico, e não só as notícias recentes.
Query por empresa: apelidos + ticker unidos por OR (ex.: "weg OR WEGE3").

Campos: link do Google (opaco, mas estável e único) -> url; título com o
sufixo " - Veículo" removido; <source url> -> veiculo; pubDate -> data_pub.
`ticker_alvo` fica NULL de propósito: quem desambigua é a entidade devolvida
pelo LLM na FASE 7 (regra travada), igual ao GDELT. A empresa buscada fica
registrada no log local de auditoria (data/gnews_coletados.jsonl).

Dedup em camadas (seção 10 do plano):
  1. url única (constraint do banco);
  2. título normalizado + veículo iguais => cópia do mesmo texto, descartada.
     Mesmo evento em veículos diferentes CONTA como notícias distintas.

Idempotência: cada par (ticker, mês) concluído é registrado em
data/gnews_progresso.jsonl e pulado em reexecuções.
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

ARQ_PROGRESSO = config.DATA_DIR / "gnews_progresso.jsonl"
ARQ_AUDIT = config.DATA_DIR / "gnews_coletados.jsonl"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
MES_INICIAL = date(2021, 1, 1)   # antes do GDELT (12/07/2021): pode recuperar o período
MES_FINAL = date(2026, 6, 1)     # fim do backtest
PAUSA_S = 1.2                    # educação com o servidor do Google
TETO_POR_MES = 25                # ver DECISOES.md item 34 (elegibilidade exige >=10)

# Queries específicas para os nomes ambíguos (armadilhas previstas no plano, seção 4).
# Sem isso, "b3" casa com toda notícia que cita a BOLSA, "motiva" é verbo comum e
# "itau" está contido em "itausa". A desambiguação FINAL continua sendo a entidade
# devolvida pelo LLM (regra travada) — isto aqui só evita inflar o corpus com ruído.
QUERIES_ESPECIFICAS = {
    "B3SA3": 'B3SA3 OR "B3 S.A." OR "bolsa B3"',
    "MOTV3": 'MOTV3 OR "Motiva Infraestrutura" OR CCRO3 OR "CCR S.A." OR "grupo CCR"',
    "ITUB4": 'ITUB4 OR "Itaú Unibanco" OR "banco Itaú"',
    "ITSA4": 'ITSA4 OR Itaúsa OR Itausa',
    "LREN3": 'LREN3 OR "Lojas Renner"',
    "ISAE4": 'ISAE4 OR "ISA Energia" OR "ISA CTEEP" OR TRPL4',
}

# Portais de conteúdo institucional/educativo — não são notícia (não medem percepção).
VEICULOS_BLOQUEADOS = {"borainvestir.b3.com.br", "b3.com.br", "edu.b3.com.br"}


def meses(inicio: date, fim: date) -> list[date]:
    saida, atual = [], inicio
    while atual <= fim:
        saida.append(atual)
        atual = date(atual.year + atual.month // 12, atual.month % 12 + 1, 1)
    return saida


def proximo_mes(m: date) -> date:
    return date(m.year + m.month // 12, m.month % 12 + 1, 1)


def normalizar_titulo(t: str) -> str:
    """Para dedup: sem acento, minúsculo, só letras/números."""
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", t).strip()[:120]


def montar_query(ticker: str, apelidos: str | None) -> str:
    if ticker in QUERIES_ESPECIFICAS:
        return QUERIES_ESPECIFICAS[ticker]
    termos = [a.strip() for a in (apelidos or "").split(";") if a.strip()]
    partes = [f'"{t}"' if " " in t else t for t in termos] + [ticker]
    return " OR ".join(partes)


def limpar_titulo(titulo: str, veiculo: str | None) -> str:
    """RSS entrega 'Título real - Veículo'; remove o sufixo do veículo."""
    if veiculo and titulo.endswith(f" - {veiculo}"):
        titulo = titulo[: -len(f" - {veiculo}")]
    return re.sub(r"\s+", " ", titulo).strip()


def buscar_mes(query: str, mes: date) -> list[dict]:
    fim = proximo_mes(mes)
    q = f"{query} after:{mes.isoformat()} before:{fim.isoformat()}"
    url = (f"https://news.google.com/rss/search?q={quote(q)}"
           f"&hl=pt-BR&gl=BR&ceid=BR:pt-150")
    r = requests.get(url, headers=HEADERS, timeout=45)
    r.raise_for_status()
    itens = []
    for it in ET.fromstring(r.content).findall(".//item"):
        link = (it.findtext("link") or "").strip()
        titulo_bruto = (it.findtext("title") or "").strip()
        if not link or not titulo_bruto:
            continue
        no_fonte = it.find("source")
        veiculo_nome = (no_fonte.text or "").strip() if no_fonte is not None else None
        veiculo_url = no_fonte.get("url") if no_fonte is not None else None
        veiculo = urlparse(veiculo_url).netloc.replace("www.", "") if veiculo_url else veiculo_nome
        try:
            data_pub = datetime.strptime(it.findtext("pubDate", "").strip(),
                                         "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        itens.append({
            "url": link,
            "titulo": limpar_titulo(titulo_bruto, veiculo_nome),
            "fonte": "google_news",
            "veiculo": veiculo,
            "data_pub": data_pub.isoformat(),
            "tom_gdelt": None,
        })
    return itens


def pares_concluidos() -> set[tuple[str, str]]:
    if not ARQ_PROGRESSO.exists():
        return set()
    feitos = set()
    for linha in ARQ_PROGRESSO.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(linha)
            feitos.add((r["ticker"], r["mes"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return feitos


def titulos_existentes() -> set[tuple[str, str]]:
    """(título normalizado, veículo) já no banco — base da dedup de cópias."""
    existentes = set()
    for n in db.selecionar("noticias", {"select": "titulo,veiculo", "titulo": "not.is.null"},
                           order="id"):
        existentes.add((normalizar_titulo(n["titulo"]), (n["veiculo"] or "").lower()))
    return existentes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teste", action="store_true", help="1 empresa x 3 meses")
    args = parser.parse_args()

    empresas = db.selecionar("universo", {"ativo": "eq.true", "select": "ticker,apelidos"},
                             order="ticker")
    todos_meses = meses(MES_INICIAL, MES_FINAL)
    if args.teste:
        empresas, todos_meses = empresas[:1], todos_meses[:3]
        print("MODO TESTE")

    feitos = pares_concluidos()
    vistos = titulos_existentes()
    print(f"Empresas: {len(empresas)} | meses: {len(todos_meses)} | "
          f"pares já coletados: {len(feitos)} | títulos no banco: {len(vistos)}")

    total_novas, total_dup, total_req, falhas = 0, 0, 0, 0
    for empresa in empresas:
        ticker = empresa["ticker"]
        query = montar_query(ticker, empresa["apelidos"])
        novas_empresa = 0
        for mes in todos_meses:
            if (ticker, mes.isoformat()) in feitos:
                continue
            time.sleep(PAUSA_S)
            try:
                itens = buscar_mes(query, mes)
                total_req += 1
            except (requests.RequestException, ET.ParseError) as e:
                falhas += 1
                print(f"  [XX] {ticker} {mes:%Y-%m}: {type(e).__name__} — será retentado no rerun")
                continue

            novos = []
            for item in itens:
                if (item["veiculo"] or "").lower() in VEICULOS_BLOQUEADOS:
                    continue  # conteúdo institucional, não notícia
                chave = (normalizar_titulo(item["titulo"]), (item["veiculo"] or "").lower())
                if chave in vistos:
                    total_dup += 1
                    continue
                vistos.add(chave)
                novos.append(item)
                if len(novos) >= TETO_POR_MES:
                    break  # RSS já vem por relevância; teto controla a cota do LLM

            if novos:
                for i in range(0, len(novos), 500):
                    db.inserir_ignorando_duplicatas("noticias", novos[i:i + 500], on_conflict="url")
            with ARQ_AUDIT.open("a", encoding="utf-8") as audit, \
                 ARQ_PROGRESSO.open("a", encoding="utf-8") as prog:
                for item in novos:
                    audit.write(json.dumps({"ticker_buscado": ticker, "mes": mes.isoformat(),
                                            "url": item["url"], "titulo": item["titulo"],
                                            "veiculo": item["veiculo"]}, ensure_ascii=False) + "\n")
                prog.write(json.dumps({"ticker": ticker, "mes": mes.isoformat(),
                                       "encontrados": len(itens), "novos": len(novos)}) + "\n")
            novas_empresa += len(novos)
            total_novas += len(novos)
        print(f"  {ticker:<8} +{novas_empresa} notícias novas", flush=True)

    print(f"\nResumo: {total_novas} novas | {total_dup} cópias descartadas | "
          f"{total_req} requisições | {falhas} falhas (rerun retenta) | custo R$ 0")
    print(f"Banco: google_news={db.contar('noticias', {'fonte': 'eq.google_news'})} | "
          f"total={db.contar('noticias')}")


if __name__ == "__main__":
    main()
