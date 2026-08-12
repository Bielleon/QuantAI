"""FASE 3 — Títulos reais das URLs sem manchete confiável.

Originalmente previsto via actor apify/cheerio-scraper; trocado por coleta
LOCAL (requests + BeautifulSoup) após a Apify passar a exigir aprovação manual
de permissões no console (HTTP 403 full-permission-actor-not-approved) e o
teste local acertar 20/20 títulos — custo R$ 0. Ver DECISOES.md itens 22 e 24.

Candidatas: (a) titulo NULL; (b) título de slug com % ambíguo (todo minúsculo
E com padrão N% — títulos reais têm maiúsculas, o que dá ponto fixo ao rerun).
Melhor título: og:title > h1 > <title>; sufixo de veículo ('| G1', '- InfoMoney')
e prefixo 'Opinião |' são sempre removidos; encoding detectado dos BYTES da
página (páginas UTF-8 sem charset no header corrompiam acentos via r.text).
Status: 200=ok | 4xx comum=morta | 401/403/429=bloqueada por anti-bot (existe,
mas não é acessível sem navegador) | 5xx/rede=transitório (rerun retenta).
Idempotência: data/titulos_processadas.jsonl (ok/morta/bloqueada não retentam);
persistência é incremental — interrupção no meio não perde o que já foi feito.
"""
import argparse
import html
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

ARQ_PROCESSADAS = config.DATA_DIR / "titulos_processadas.jsonl"
ARQ_AUDIT = config.DATA_DIR / "titulos_resultados.jsonl"
WORKERS = 8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

_JUNK = re.compile(r"não encontrad|not found|page unavailable|^\s*404\b|acesso negado|access denied|forbidden|attention required|just a moment", re.I)
_SUFIXO_VEICULO = re.compile(
    r"\s*[|\-–—]\s*(g1|globo(\.com)?|exame(\s+insight)?|infomoney|estad[aã]o(\s+e-investidor)?|e-investidor"
    r"|uol(\s+(economia|notícias))?|folha(\s+de\s+s\.?\s?paulo)?|cnn(\s+brasil)?|reuters|bloomberg(\s+l[ií]nea)?"
    r"|valor(\s+econ[oô]mico)?|braziljournal|wsj)\s*$", re.I)
_PREFIXO_SECAO = re.compile(r"^opini[aã]o\s*\|\s*", re.I)
_SECOES_SEM_MANCHETE = ("forum-dos-leitores",)  # compilações sem manchete própria


def _limpar(texto: str | None, cortar_sufixo_generico: bool = False) -> str | None:
    if not texto:
        return None
    t = re.sub(r"\s+", " ", html.unescape(texto)).strip()
    while True:  # sufixos de veículo podem vir aninhados ('... | Economia | G1')
        novo = _SUFIXO_VEICULO.sub("", t).strip()
        if novo == t:
            break
        t = novo
    t = _PREFIXO_SECAO.sub("", t).strip()
    if cortar_sufixo_generico and len(t) > 30:
        t = re.sub(r"\s*[|\-–—]\s*[^|\-–—]{2,40}$", "", t).strip()
    if len(t) < 15 or _JUNK.search(t):
        return None
    return t


def limpar_titulo_gravado(t: str) -> str | None:
    """Reaplica só o saneamento de string (sem refetch) a um título já existente."""
    return _limpar(t)


def extrair_titulo(html_bytes: bytes) -> str | None:
    # bytes (não r.text): o BeautifulSoup detecta o charset do meta/BOM, evitando
    # mojibake quando o header HTTP não declara charset (caso da Folha)
    soup = BeautifulSoup(html_bytes, "html.parser")
    og = soup.find("meta", property="og:title")
    h1 = soup.h1.get_text(strip=True) if soup.h1 else None
    title = soup.title.get_text(strip=True) if soup.title else None
    return (_limpar(og.get("content") if og else None)
            or _limpar(h1)
            or _limpar(title, cortar_sufixo_generico=True))


def urls_ja_processadas() -> set[str]:
    if not ARQ_PROCESSADAS.exists():
        return set()
    feitas = set()
    for linha in ARQ_PROCESSADAS.read_text(encoding="utf-8").splitlines():
        try:
            feitas.add(json.loads(linha)["url"])
        except (json.JSONDecodeError, KeyError):
            continue
    return feitas


def candidatas() -> list[dict]:
    nulas = db.selecionar("noticias", {"fonte": "eq.gdelt", "titulo": "is.null", "select": "id,url,titulo"},
                          order="id")
    com_titulo = db.selecionar("noticias", {"fonte": "eq.gdelt", "titulo": "not.is.null", "select": "id,url,titulo"},
                               order="id")
    # ambíguas: padrão N% sem vírgula E título todo minúsculo (assinatura de slug —
    # títulos reais têm maiúsculas, então um título já corrigido não volta à fila)
    ambiguas = [n for n in com_titulo
                if re.search(r"\d+%", n["titulo"]) and "," not in n["titulo"]
                and n["titulo"] == n["titulo"].lower()]
    feitas = urls_ja_processadas()
    fila = [n for n in nulas + ambiguas
            if n["url"] not in feitas
            and not any(s in n["url"] for s in _SECOES_SEM_MANCHETE)]
    print(f"Candidatas: {len(nulas)} sem título + {len(ambiguas)} com % ambíguo "
          f"-> fila de {len(fila)} (excluídas: já processadas e seções sem manchete)")
    random.Random(42).shuffle(fila)  # intercala domínios (educação com os servidores)
    return fila


def buscar(noticia: dict) -> dict:
    """{'url', 'status': 'ok'|'morta'|'bloqueada'|'transitorio', ...}."""
    url = noticia["url"]
    time.sleep(0.1)
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        return {"url": url, "status": "transitorio", "erro": type(e).__name__}
    if r.status_code >= 500:
        return {"url": url, "status": "transitorio", "erro": f"HTTP {r.status_code}"}
    if r.status_code in (401, 403, 429):
        # anti-bot/paywall: a página existe, mas não sai sem navegador — não retentar
        return {"url": url, "status": "bloqueada", "erro": f"HTTP {r.status_code}"}
    if r.status_code != 200:
        return {"url": url, "status": "morta", "erro": f"HTTP {r.status_code}"}
    titulo = extrair_titulo(r.content)
    if not titulo:
        return {"url": url, "status": "morta", "erro": "sem título aproveitável"}
    return {"url": url, "status": "ok", "titulo": titulo}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teste", action="store_true", help="roda só 20 URLs")
    args = parser.parse_args()

    fila = candidatas()
    if not fila:
        print("Nada a fazer — fila vazia.")
        return
    if args.teste:
        fila = fila[:20]
        print("MODO TESTE: 20 URLs")

    contagem = {"ok": 0, "morta": 0, "bloqueada": 0, "transitorio": 0}
    # persistência INCREMENTAL: cada resultado é gravado assim que chega —
    # interrupção no meio preserva tudo que já foi buscado
    with ThreadPoolExecutor(max_workers=WORKERS) as pool, \
         ARQ_AUDIT.open("a", encoding="utf-8") as audit, \
         ARQ_PROCESSADAS.open("a", encoding="utf-8") as proc:
        futuros = {pool.submit(buscar, n): n for n in fila}
        for fut in as_completed(futuros):
            r = fut.result()
            contagem[r["status"]] += 1
            if r["status"] == "transitorio":
                continue  # fora dos logs: rerun retenta
            audit.write(json.dumps(r, ensure_ascii=False) + "\n")
            audit.flush()
            if r["status"] == "ok":
                db.atualizar("noticias", {"url": f"eq.{r['url']}"}, {"titulo": r["titulo"]})
            proc.write(json.dumps({"url": r["url"], "status": r["status"],
                                   "titulo_novo": r.get("titulo")}, ensure_ascii=False) + "\n")
            proc.flush()

    ainda_null = db.contar("noticias", {"fonte": "eq.gdelt", "titulo": "is.null"})
    print(f"\nResumo: {contagem['ok']} títulos atualizados | {contagem['morta']} mortas | "
          f"{contagem['bloqueada']} bloqueadas por anti-bot | {contagem['transitorio']} transitórios (rerun retenta) | "
          f"ainda sem título: {ainda_null} | custo R$ 0")


if __name__ == "__main__":
    main()
