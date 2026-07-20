"""FASE 2 — Notícias históricas do GDELT.

Importa 'noticias_finais.csv' para a tabela `noticias` com o mapeamento
aprovado (DECISOES.md item 19): DATE→data_pub (UTC), url→url, fonte→veiculo,
tom(1º valor)→tom_gdelt, fonte='gdelt'. O título é extraído do slug da URL;
URLs sem slug legível ficam com titulo NULL (candidatas ao Cheerio na FASE 3).
Idempotente: INSERT ... ON CONFLICT (url) DO NOTHING, em lotes de 500.
"""
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

ARQUIVO_CSV = config.RAIZ / "noticias_finais.csv"
LOTE = 500
MIN_PALAVRAS_SLUG = 4  # slug com menos que isso não é manchete confiável


def _tres_tokens_sao_data(tokens: list[str]) -> bool:
    """True se os 3 tokens formam data 'aaaa mm dd' (Reuters) ou 'dd mm aaaa' (Estadão)."""
    a, b, c = tokens
    if not (a.isdigit() and b.isdigit() and c.isdigit()):
        return False
    return (len(a) == 4 and 1 <= int(b) <= 12 and 1 <= int(c) <= 31) or \
           (len(c) == 4 and 1 <= int(b) <= 12 and 1 <= int(a) <= 31)


def titulo_do_slug(url: str) -> str | None:
    """Extrai manchete legível do último segmento do caminho da URL, ou None.

    Regras endurecidas após revisão (DECISOES.md item 21):
    - ids numéricos longos só são removidos nas BORDAS do slug (no meio podem
      ser valores da manchete, ex. 'lucro-r-44561-bilhoes' = R$ 445,61 bi);
    - padrão antigo do Estadão 'secao,slug,id' -> fica o pedaço com mais palavras;
    - datas soltas no fim (Reuters '...-2021-12-07') são descartadas;
    - 'NNpercent' vira 'NN%' (decimal do slug é irrecuperável — limitação declarada);
    - título final sempre sem espaços duplicados/das pontas.
    """
    try:
        caminho = unquote(urlparse(url).path)
    except ValueError:
        return None
    segmentos = [s for s in caminho.split("/") if s]
    if not segmentos:
        return None
    ult = segmentos[-1]
    ult = re.sub(r"\.(s?html?|ghtml|xhtml|php|aspx?|jsp|amp)$", "", ult, flags=re.I)
    if "," in ult:  # estadão antigo: 'geral,slug-com-hifens,70003938517'
        ult = max(ult.split(","), key=lambda p: len(re.split(r"[-_+]", p)))
    ult = re.sub(r"^\d{5,}([-_]+|$)", "", ult)   # id colado no início
    ult = re.sub(r"([-_]+|^)\d{5,}$", "", ult)   # id colado no fim
    # ids das bordas já foram removidos acima; número longo NO MEIO do slug é
    # valor da manchete (ex. 'lucro-r-44561-bilhoes' = R$ 445,61 bi) — mantido
    palavras = [p for p in re.split(r"[-_+\s,]+", ult) if p]
    if len(palavras) >= 3 and _tres_tokens_sao_data(palavras[-3:]):
        palavras = palavras[:-3]
    palavras = [re.sub(r"^(\d+)percent$", r"\1%", p) for p in palavras]
    if len(palavras) < MIN_PALAVRAS_SLUG:
        return None
    return " ".join(palavras).strip()


def data_gdelt_para_iso(valor: str) -> str | None:
    """Converte AAAAMMDDHHMMSS (UTC, formato GDELT) para ISO-8601 com timezone."""
    try:
        return datetime.strptime(valor.strip(), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def tom_gdelt(valor: str) -> float | None:
    """1º dos 7 números do campo V2Tone do GDELT (tone geral)."""
    try:
        return float(valor.split(",")[0])
    except (ValueError, AttributeError, IndexError):
        return None


def carregar_linhas() -> list[dict]:
    linhas = []
    with ARQUIVO_CSV.open(encoding="utf-8-sig") as f:
        leitor = csv.DictReader(f)
        obrigatorias = {"DATE", "url", "fonte", "tom"}
        faltando = obrigatorias - set(leitor.fieldnames or [])
        if faltando:
            raise SystemExit(f"Cabeçalho inesperado no CSV: faltam colunas {sorted(faltando)}. Abortando.")
        for r in leitor:
            url = (r.get("url") or "").strip()
            if not url:
                continue
            linhas.append({
                "url": url,
                "titulo": titulo_do_slug(url),
                "fonte": "gdelt",
                "veiculo": (r.get("fonte") or "").strip() or None,
                "data_pub": data_gdelt_para_iso(r.get("DATE", "")),
                "tom_gdelt": tom_gdelt(r.get("tom", "")),
            })
    return linhas


def main():
    linhas = carregar_linhas()
    sem_titulo = sum(1 for l in linhas if l["titulo"] is None)
    sem_data = sum(1 for l in linhas if l["data_pub"] is None)
    print(f"CSV: {len(linhas)} notícias | sem título via slug: {sem_titulo} | sem data: {sem_data}")
    if sem_data:
        raise SystemExit(f"{sem_data} linhas com DATE inválida — investigar antes de importar.")

    # dedup dentro do próprio arquivo (por segurança; inspeção achou 0)
    vistos: set[str] = set()
    unicas = [l for l in linhas if not (l["url"] in vistos or vistos.add(l["url"]))]
    if len(unicas) < len(linhas):
        print(f"Dedup interno: {len(linhas) - len(unicas)} URLs repetidas no CSV ignoradas")

    antes = db.contar("noticias", {"fonte": "eq.gdelt"})
    for i in range(0, len(unicas), LOTE):
        db.inserir_ignorando_duplicatas("noticias", unicas[i:i + LOTE], on_conflict="url")
        print(f"  lote {i // LOTE + 1}/{-(-len(unicas) // LOTE)} enviado", end="\r")
    depois = db.contar("noticias", {"fonte": "eq.gdelt"})
    print(f"\nBanco: {antes} -> {depois} notícias gdelt (novas: {depois - antes}; já existentes: {len(unicas) - (depois - antes)})")

    sem_titulo_banco = db.contar("noticias", {"fonte": "eq.gdelt", "titulo": "is.null"})
    print(f"Sem título no banco (candidatas ao Cheerio/FASE 3): {sem_titulo_banco}")

    print("\nAmostra importada:")
    for n in db.selecionar("noticias", {"fonte": "eq.gdelt", "select": "data_pub,veiculo,tom_gdelt,titulo"},
                           order="data_pub", limite=5):
        titulo = (n["titulo"] or "(sem título)")[:80]
        print(f"  {n['data_pub'][:10]} | {(n['veiculo'] or '?'):<18} | {titulo}")


if __name__ == "__main__":
    main()
