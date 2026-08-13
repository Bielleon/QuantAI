"""FASE 7 — Classificação de sentimento das manchetes (prompt congelado 9.1).

Lotes de 25 manchetes por chamada, temperature 0, modelo PINADO, prompt_hash
sha256 gravado por classificação. Idempotente: só processa notícias que ainda
não têm linha em `sentimentos`. Checkpoint a cada lote (grava antes de seguir),
parada limpa em cota/instabilidade — reexecutar continua de onde parou.

Desambiguação (regra travada): a empresa de cada notícia é decidida pela
ENTIDADE devolvida pelo LLM, mapeada para ticker por palavra inteira contra
nome/apelidos do universo. Notícia cuja entidade não casa com o universo fica
com ticker_mapeado NULL e não conta para nenhuma empresa (mas conta no MERCADO).

Uso:
  07_classificacao_llm.py piloto        -> 200 manchetes + distribuição (antes do lote total)
  07_classificacao_llm.py completo      -> todas as pendentes
  07_classificacao_llm.py gabarito      -> exporta 150 manchetes estratificadas p/ rotulagem humana
"""
import argparse
import hashlib
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db, gemini_client  # noqa: E402

TAM_LOTE = 25

PROMPT_SENTIMENTO = """Você é um economista e analista de investimentos do mercado financeiro
brasileiro.
Para CADA notícia numerada abaixo, classifique o sentimento DO PONTO DE
VISTA DE UM INVESTIDOR da empresa citada — ou seja, o impacto provável da
notícia sobre o preço da ação, e não o tom emocional do texto.
Regras:
- Responda APENAS com uma linha JSON por notícia, sem nenhum texto extra:
  {"id": N, "sentimento": "positivo" | "negativo" | "neutro", "entidade": "empresa principal"}
- "positivo" = tende a valorizar a ação (lucro acima do esperado, dividendos,
  novo contrato, recomendação de compra, recorde de vendas...)
- "negativo" = tende a desvalorizar (prejuízo, queda de receita, dívida,
  investigação, multa, rebaixamento, greve, acidente...)
- "neutro" = sem impacto claro no preço (obituário, curiosidade, evento
  institucional, notícia que só cita a empresa de passagem, macro genérica)
- Se a notícia citar mais de uma empresa, escolha a mais central como entidade.
- Julgue exclusivamente pelo texto fornecido; não use conhecimento sobre o
  que aconteceu depois da notícia.
Notícias:
{LISTA_NUMERADA}"""
PROMPT_HASH = hashlib.sha256(PROMPT_SENTIMENTO.encode("utf-8")).hexdigest()


def _sem_acento(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def construir_mapa_entidades() -> dict[str, re.Pattern]:
    """ticker -> regex de palavra inteira com apelidos + ticker + nome curto."""
    mapa = {}
    for e in db.selecionar("universo", {"ativo": "eq.true", "select": "ticker,nome,apelidos"},
                           order="ticker"):
        termos = {_sem_acento(a) for a in (e["apelidos"] or "").split(";") if a.strip()}
        termos.add(e["ticker"].lower())
        # primeira palavra significativa da razão social (ex.: "multiplan")
        for palavra in _sem_acento(e["nome"]).split():
            if len(palavra) >= 5 and palavra not in {"companhia", "banco", "lojas", "grupo"}:
                termos.add(palavra)
                break
        mapa[e["ticker"]] = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in sorted(termos, key=len, reverse=True)) + r")(?![a-z0-9])")
    return mapa


def mapear_ticker(entidade: str, mapa: dict[str, re.Pattern]) -> str | None:
    """Casa a entidade do LLM com um ticker. Mais específico vence (itausa > itau)."""
    ent = _sem_acento(entidade)
    casados = [t for t, padrao in mapa.items() if padrao.search(ent)]
    if not casados:
        return None
    if len(casados) == 1:
        return casados[0]
    # desempate: o ticker cujo termo casado é o mais longo (ITSA4 'itausa' > ITUB4 'itau')
    def tamanho_casado(t: str) -> int:
        m = mapa[t].search(ent)
        return len(m.group(0)) if m else 0
    return max(casados, key=tamanho_casado)


def noticias_pendentes(limite: int | None = None) -> list[dict]:
    """Notícias com título e SEM sentimento ainda (idempotência)."""
    ja_feitas = {s["noticia_id"] for s in db.selecionar("sentimentos", {"select": "noticia_id"},
                                                        order="noticia_id")}
    pendentes = []
    for n in db.selecionar("noticias", {"select": "id,titulo,data_pub", "titulo": "not.is.null"},
                           order="id"):
        if n["id"] not in ja_feitas:
            pendentes.append(n)
            if limite and len(pendentes) >= limite:
                break
    return pendentes


def _parse_linhas_json(texto: str) -> dict[int, dict]:
    saida = {}
    for m in re.finditer(r"\{[^{}]*\}", texto):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if "id" in obj and "sentimento" in obj:
            try:
                saida[int(obj["id"])] = obj
            except (TypeError, ValueError):
                continue
    return saida


def classificar(noticias: list[dict], mapa: dict[str, re.Pattern]) -> dict:
    cliente = gemini_client.GeminiClient()
    contagem = Counter()
    gravados, sem_resposta = 0, 0
    try:
        for i in range(0, len(noticias), TAM_LOTE):
            lote = noticias[i:i + TAM_LOTE]
            lista = "\n".join(f'{j + 1}. {n["titulo"]}' for j, n in enumerate(lote))
            resposta = cliente.gerar(PROMPT_SENTIMENTO.replace("{LISTA_NUMERADA}", lista))
            respostas = _parse_linhas_json(resposta["texto"])

            linhas = []
            for j, n in enumerate(lote, start=1):
                r = respostas.get(j)
                if not r or r.get("sentimento") not in ("positivo", "negativo", "neutro"):
                    sem_resposta += 1
                    continue
                entidade = str(r.get("entidade") or "")
                linhas.append({
                    "noticia_id": n["id"],
                    "sentimento": r["sentimento"],
                    "entidade_llm": entidade[:200] or None,
                    "ticker_mapeado": mapear_ticker(entidade, mapa),
                    "modelo": config.GEMINI_MODEL_PINNED,
                    "model_version": resposta["model_version"],
                    "prompt_hash": PROMPT_HASH,
                })
                contagem[r["sentimento"]] += 1
            if linhas:  # checkpoint: grava o lote antes de seguir
                db.upsert("sentimentos", linhas, on_conflict="noticia_id")
                gravados += len(linhas)
            if (i // TAM_LOTE + 1) % 10 == 0:
                print(f"  {gravados}/{len(noticias)} classificadas {dict(contagem)}", flush=True)
    except gemini_client.CotaEsgotadaError:
        print("\n!! Cota Gemini esgotada — parada limpa. Reexecute depois (continua do ponto).", flush=True)
    except gemini_client.ServicoIndisponivelError as e:
        print(f"\n!! Gemini instável ({e}) — parada limpa. Reexecute mais tarde.", flush=True)
    return {"gravados": gravados, "sem_resposta": sem_resposta, "distribuicao": dict(contagem)}


def mostrar_distribuicao():
    linhas = db.selecionar("sentimentos", {"select": "sentimento,ticker_mapeado"}, order="id")
    total = len(linhas)
    if not total:
        print("Nenhuma classificação ainda.")
        return
    print(f"\n=== Distribuição ({total} manchetes classificadas) ===")
    for s, n in Counter(l["sentimento"] for l in linhas).most_common():
        print(f"  {s:<10} {n:>7} ({100 * n / total:>5.1f}%)")
    mapeadas = sum(1 for l in linhas if l["ticker_mapeado"])
    print(f"  {'-' * 30}\n  mapeadas para empresa do universo: {mapeadas} ({100 * mapeadas / total:.1f}%)")
    print("\n  Top empresas por volume classificado:")
    for t, n in Counter(l["ticker_mapeado"] for l in linhas if l["ticker_mapeado"]).most_common(8):
        print(f"    {t:<8} {n}")


def exportar_gabarito(n_amostra: int = 150):
    """150 manchetes estratificadas por sentimento p/ rotulagem humana (mede acurácia)."""
    import csv
    linhas = db.selecionar("sentimentos", {"select": "noticia_id,sentimento,entidade_llm,ticker_mapeado"},
                           order="noticia_id")
    if not linhas:
        raise SystemExit("Rode a classificação antes de exportar o gabarito.")
    titulos = {n["id"]: n["titulo"] for n in
               db.selecionar("noticias", {"select": "id,titulo", "titulo": "not.is.null"}, order="id")}
    por_sent: dict[str, list] = {}
    for l in linhas:
        por_sent.setdefault(l["sentimento"], []).append(l)
    rng = random.Random(42)  # seed fixa: replicabilidade
    amostra = []
    por_grupo = n_amostra // max(1, len(por_sent))
    for sent, grupo in por_sent.items():
        rng.shuffle(grupo)
        amostra.extend(grupo[:por_grupo])
    rng.shuffle(amostra)
    destino = config.OUTPUTS_DIR / "gabarito.csv"
    with destino.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["noticia_id", "titulo", "sentimento_llm", "entidade_llm", "ticker_mapeado",
                    "sentimento_humano_PREENCHER"])
        for l in amostra:
            w.writerow([l["noticia_id"], titulos.get(l["noticia_id"], ""), l["sentimento"],
                        l["entidade_llm"], l["ticker_mapeado"], ""])
    print(f"Gabarito com {len(amostra)} manchetes: {destino}")
    print("Preencha a coluna 'sentimento_humano_PREENCHER' com positivo/negativo/neutro.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("modo", choices=["piloto", "completo", "gabarito", "distribuicao"])
    args = parser.parse_args()

    if args.modo == "gabarito":
        exportar_gabarito()
        return
    if args.modo == "distribuicao":
        mostrar_distribuicao()
        return

    mapa = construir_mapa_entidades()
    limite = 200 if args.modo == "piloto" else None
    pendentes = noticias_pendentes(limite)
    print(f"Manchetes a classificar: {len(pendentes)}"
          + (" (PILOTO — revise a distribuição antes do lote completo)" if args.modo == "piloto" else ""))
    if not pendentes:
        mostrar_distribuicao()
        return
    r = classificar(pendentes, mapa)
    print(f"\nResumo: {r['gravados']} gravadas | {r['sem_resposta']} sem resposta válida "
          f"(ficam pendentes) | {r['distribuicao']}")
    mostrar_distribuicao()


if __name__ == "__main__":
    main()
