"""FASE 4 — Documentos CVM (IPE) e extrator de veto.

Etapas (rodadas separadamente; todas idempotentes):
  metadados : baixa ipe_cia_aberta_2021..2026.zip (CSV ; latin-1), filtra empresas
              do universo (razão social normalizada OU apelido como palavra inteira)
              e Categoria em {'Fato Relevante','Comunicado ao Mercado'}; upsert em
              documentos_cvm por link_download (status 'pendente').
  pdfs      : baixa o PDF de cada doc 'pendente' e extrai texto com pdfplumber
              (curto/escaneado => 'vazio_ou_escaneado'; sem OCR no MVP).
  veto      : roda o EXTRATOR DE VETO (prompt congelado 9.2, temperature 0, modelo
              pinado, prompt_hash sha256) nos docs 'extraido'; valida a citação
              literal POR CÓDIGO (não literal => flag REJEITADA, sem veto);
              flag válida => veto de mes_evento até mes_evento + 6 meses.
              Parada limpa em cota esgotada (reexecutar depois continua do ponto).
Documentos CVM NÃO geram sentimento — só veto (regra travada).

Uso: 04_cvm_ipe.py {metadados|pdfs|veto} [--piloto N]
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db, gemini_client  # noqa: E402

ANOS = range(2021, 2027)
URL_IPE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
DIR_ZIPS = config.DATA_DIR / "cvm"
DIR_PDFS = config.DATA_DIR / "cvm_pdfs"
CATEGORIAS = {"Fato Relevante", "Comunicado ao Mercado"}
MAX_CHARS_TEXTO = 30_000   # fatos relevantes têm 1-3 páginas; teto protege o banco
MIN_CHARS_TEXTO = 200      # menos que isso = PDF escaneado/vazio

PROMPT_VETO = """Você é um auditor de riscos. Abaixo está o texto de um documento oficial
(fato relevante ou comunicado ao mercado) de uma empresa listada na B3.
Sua ÚNICA tarefa: identificar se o documento comunica um RISCO FUNDAMENTALISTA
GRAVE, definido EXCLUSIVAMENTE como uma destas categorias:
- recuperacao_judicial_ou_falencia
- fraude_ou_irregularidade_contabil
- duvida_continuidade_operacional
- default_ou_inadimplencia_de_divida_relevante
- intervencao_regulatoria_grave
Responda APENAS este JSON:
{"flag_grave": true|false, "tipo_risco": "<categoria ou null>",
 "citacao_literal": "<trecho copiado EXATAMENTE do texto, máx. 300 caracteres, ou null>"}
Regras:
- A citacao_literal deve ser cópia EXATA de um trecho do texto fornecido.
- Notícia negativa comum (queda de lucro, multa pequena, saída de executivo,
  revisão de guidance) NÃO é risco grave: flag_grave=false.
- Não use conhecimento externo ao texto.
Texto do documento:
{TEXTO}"""
PROMPT_VETO_HASH = hashlib.sha256(PROMPT_VETO.encode("utf-8")).hexdigest()


# ---------- etapa METADADOS ----------

# Nomes registrais na CVM que o matching automático não alcança (formas normalizadas).
# SBSP3: CVM usa 'ESTADO' por extenso e o nome não contém 'SABESP'.
NOMES_EXTRAS = {
    "SBSP3": ["CIA SANEAMENTO BASICO ESTADO SAO PAULO"],
}
# Entidades que casam por apelido mas NÃO são a empresa do universo (falsos positivos
# vistos na auditoria de 2026-07-20): subsidiárias/veículos do grupo BTG.
NOMES_EXCLUIDOS = {
    "BTG PACTUAL COMMODITIES SERTRADING",
    "BTG PACTUAL PARTICIPATIONS LTD",
}


def normalizar_nome(nome: str) -> str:
    n = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    n = n.upper()
    n = re.sub(r"\bS[/.]?\s?A\b\.?", " ", n)          # S.A. / S/A / SA
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def carregar_universo() -> list[dict]:
    empresas = db.selecionar("universo", {"ativo": "eq.true", "select": "ticker,nome,apelidos"}, order="ticker")
    for e in empresas:
        e["nome_norm"] = normalizar_nome(e["nome"])
        e["apelidos_norm"] = [normalizar_nome(a) for a in (e["apelidos"] or "").split(";") if a.strip()]
    return empresas


def casar_empresa(nome_cvm_norm: str, empresas: list[dict]) -> str | None:
    """Devolve o ticker do universo que casa com o nome CVM normalizado, ou None."""
    if nome_cvm_norm in NOMES_EXCLUIDOS:
        return None
    for ticker, nomes in NOMES_EXTRAS.items():
        if nome_cvm_norm in nomes:
            return ticker
    for e in empresas:
        if e["nome_norm"] and (e["nome_norm"] == nome_cvm_norm
                               or e["nome_norm"] in nome_cvm_norm
                               or nome_cvm_norm in e["nome_norm"] and len(nome_cvm_norm) >= 6):
            return e["ticker"]
        for ap in e["apelidos_norm"]:
            if re.search(rf"(?<![A-Z0-9]){re.escape(ap)}(?![A-Z0-9])", nome_cvm_norm):
                return e["ticker"]
    return None


def etapa_metadados():
    DIR_ZIPS.mkdir(parents=True, exist_ok=True)
    empresas = carregar_universo()
    nomes_casados: dict[str, set[str]] = {}
    docs = []
    for ano in ANOS:
        destino = DIR_ZIPS / f"ipe_cia_aberta_{ano}.zip"
        # ano corrente muda todo dia — sempre rebaixar; anos fechados são estáveis
        if not destino.exists() or ano == date.today().year:
            r = requests.get(URL_IPE.format(ano=ano), timeout=180)
            r.raise_for_status()
            destino.write_bytes(r.content)
        z = zipfile.ZipFile(destino)
        with z.open(z.namelist()[0]) as f:
            leitor = csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";")
            cache: dict[str, str | None] = {}
            for r in leitor:
                if r["Categoria"] not in CATEGORIAS:
                    continue
                nome_norm = normalizar_nome(r["Nome_Companhia"])
                if nome_norm not in cache:
                    cache[nome_norm] = casar_empresa(nome_norm, empresas)
                ticker = cache[nome_norm]
                if not ticker:
                    continue
                nomes_casados.setdefault(ticker, set()).add(r["Nome_Companhia"])
                docs.append({
                    "ticker": ticker,
                    "categoria": r["Categoria"],
                    "tipo": (r.get("Tipo") or "").strip() or None,
                    "assunto": (r.get("Assunto") or "").strip()[:500] or None,
                    "data_entrega": r["Data_Entrega"][:10] or None,
                    "link_download": r["Link_Download"].strip(),
                })
        print(f"  {ano}: acumulado {len(docs)} documentos do universo")

    # dedup por link (mesmo link pode reaparecer em versões)
    vistos: set[str] = set()
    unicos = [d for d in docs if not (d["link_download"] in vistos or vistos.add(d["link_download"]))]
    for i in range(0, len(unicos), 500):
        db.inserir_ignorando_duplicatas("documentos_cvm", unicos[i:i + 500], on_conflict="link_download")
    total = db.contar("documentos_cvm")

    print(f"\nDocumentos no banco: {total} (enviados {len(unicos)} únicos de {len(docs)} linhas)")
    print("\nAUDITORIA — nomes CVM casados por ticker (confira falsos positivos!):")
    for ticker in sorted(nomes_casados):
        for nome in sorted(nomes_casados[ticker]):
            print(f"  {ticker:<8} <- {nome}")
    print("\nContagem por ticker (desta carga):")
    por_ticker: dict[str, int] = {}
    for d in unicos:
        por_ticker[d["ticker"]] = por_ticker.get(d["ticker"], 0) + 1
    for t, n in sorted(por_ticker.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<8} {n}")


# ---------- etapa PDFS ----------

def _extrair_worker(caminho: str, fila):
    """Roda em SUBPROCESSO: extrai texto (pypdfium2 primeiro — rápido e tolerante;
    pdfplumber como fallback) e devolve pela fila. Isolado porque PDFs patológicos
    podem travar a extração para sempre — o pai mata o subprocesso no timeout."""
    texto = ""
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(caminho)
        texto = "\n".join(p.get_textpage().get_text_range() for p in pdf)
        pdf.close()
    except Exception:  # noqa: BLE001
        texto = ""
    if len(texto.strip()) < MIN_CHARS_TEXTO:
        try:
            import pdfplumber
            with pdfplumber.open(caminho) as pdf:
                texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:  # noqa: BLE001
            texto = ""
    # truncar ANTES de enviar: mantém a carga pequena no pipe (o resto seria descartado)
    fila.put(texto[:MAX_CHARS_TEXTO])


def _extrair_com_watchdog(caminho: Path, timeout_s: int = 90) -> str | None:
    """None = falha/travamento; string = texto (pode ser vazio).

    O get() vem ANTES do join(): se o pai esperasse o filho terminar primeiro,
    textos maiores que o buffer do pipe travariam o filho na escrita — deadlock
    que o watchdog interpretava como 'PDF travado' e descartava justamente os
    documentos mais longos (DECISOES.md item 25).
    """
    import multiprocessing as mp
    import queue as queuemod
    fila = mp.Queue()
    p = mp.Process(target=_extrair_worker, args=(str(caminho), fila))
    p.start()
    try:
        texto = fila.get(timeout=timeout_s)
    except queuemod.Empty:
        p.terminate()
        p.join()
        return None
    p.join(10)
    if p.is_alive():
        p.terminate()
        p.join()
    return texto


def _eh_pdf(caminho: Path) -> bool:
    """A CVM devolve HTTP 200 com a página HTML 'ENET Download de Documento'
    quando está sob carga; sem checar a assinatura, esse HTML era salvo como
    .pdf e o documento virava 'vazio_ou_escaneado' para sempre (item 29)."""
    try:
        with caminho.open("rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _baixar_e_extrair(doc: dict) -> tuple[int, str, str | None]:
    """Devolve (id, novo_status, texto)."""
    destino = DIR_PDFS / f"{doc['id']}.pdf"
    if destino.exists() and not _eh_pdf(destino):
        destino.unlink()  # HTML de erro de execução anterior
    try:
        for tentativa in range(3):
            if destino.exists() and destino.stat().st_size > 0:
                break
            time.sleep(0.2 + tentativa * 2)  # backoff: o erro é rate-limit intermitente
            r = requests.get(doc["link_download"], timeout=60)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                destino.write_bytes(r.content)
                break
        else:
            return doc["id"], "erro_download", None
    except requests.RequestException:
        return doc["id"], "erro_download", None
    if not destino.exists() or not _eh_pdf(destino):
        return doc["id"], "erro_download", None
    texto = _extrair_com_watchdog(destino)
    if texto is None:
        return doc["id"], "erro_extracao", None
    texto = texto.strip()
    if len(texto) < MIN_CHARS_TEXTO:
        return doc["id"], "vazio_ou_escaneado", None
    return doc["id"], "extraido", texto[:MAX_CHARS_TEXTO]


def etapa_pdfs(retentar_erros: bool = False):
    DIR_PDFS.mkdir(parents=True, exist_ok=True)
    status_alvo = "in.(pendente,erro_download,erro_extracao)" if retentar_erros else "eq.pendente"
    fila = db.selecionar("documentos_cvm", {"status_processamento": status_alvo,
                                            "select": "id,link_download"}, order="id")
    if retentar_erros:
        # erro_extracao costuma ser PDF truncado por interrupção — força rebaixar
        for d in fila:
            arquivo = DIR_PDFS / f"{d['id']}.pdf"
            if arquivo.exists():
                arquivo.unlink()
    print(f"PDFs a processar: {len(fila)}", flush=True)
    feitos = 0
    contagem: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = {pool.submit(_baixar_e_extrair, d): d for d in fila}
        for fut in as_completed(futuros):
            doc_id, status, texto = fut.result()
            valores = {"status_processamento": status}
            if texto:
                valores["texto_extraido"] = texto
            db.atualizar("documentos_cvm", {"id": f"eq.{doc_id}"}, valores)
            contagem[status] = contagem.get(status, 0) + 1
            feitos += 1
            if feitos % 100 == 0:
                print(f"  {feitos}/{len(fila)} processados {contagem}", flush=True)
    print(f"\nResultado: {contagem}", flush=True)


# ---------- PRÉ-FILTRO DE RISCO ----------
# Motivo (DECISOES.md item 35): mandar os 5.065 documentos ao LLM custaria ~8,3M
# tokens — inviável na cota gratuita. Um documento que não cita NENHUM termo
# relacionado às 5 categorias de risco grave não pode ser um veto; ele é
# descartado por CÓDIGO (status 'sem_indicio_risco'), sem consumir cota.
# A lista é deliberadamente GENEROSA (prioriza recall): qualquer indício manda o
# documento ao LLM, que continua sendo o único juiz do que é risco grave.
_TERMOS_RISCO = [
    # recuperação judicial / falência
    "recuperacao judicial", "recuperacao extrajudicial", "falencia", "insolven", "concordata",
    # fraude / irregularidade contábil
    "fraude", "fraudulent", "irregularidade contabil", "manipulacao", "desvio de recurso",
    "malversacao", "republicacao das demonstracoes", "reapresentacao das demonstracoes",
    "erro contabil", "acordo de leniencia", "corrupcao", "lavagem de dinheiro", "improbidade",
    "delacao", "busca e apreensao", "policia federal", "operacao da",
    # dúvida sobre continuidade
    "continuidade operacional", "continuidade dos negocios", "going concern", "descontinuidade",
    "impairment relevante", "prejuizo recorrente", "deterioracao",
    # default / inadimplência
    "default", "inadimplen", "inadimplem", "vencimento antecipado", "covenant", "waiver",
    "moratoria", "calote",
    # intervenção regulatória grave
    "intervencao", "liquidacao extrajudicial", "cassacao", "inidonei", "caducidade",
    "suspensao da concessao", "revogacao da concessao", "medida cautelar", "afastamento do",
    "tribunal de contas", "cvm instaurou", "processo administrativo sancionador",
]
PADRAO_RISCO = re.compile("|".join(_TERMOS_RISCO))
PREFILTRO_VERSAO = "v1-2026-08-13"


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()


def tem_indicio_de_risco(texto: str) -> bool:
    return bool(PADRAO_RISCO.search(_sem_acento(texto)))


def etapa_prefiltro():
    """Marca como 'sem_indicio_risco' os documentos sem qualquer termo de risco."""
    fila = db.selecionar("documentos_cvm",
                         {"status_processamento": "eq.extraido", "select": "id,texto_extraido"},
                         order="id")
    descartados = [d["id"] for d in fila if not tem_indicio_de_risco(d["texto_extraido"])]
    print(f"Documentos analisados: {len(fila)} | sem indício de risco: {len(descartados)} "
          f"({100 * len(descartados) / max(1, len(fila)):.0f}%) | vão ao LLM: {len(fila) - len(descartados)}")
    for i in range(0, len(descartados), 200):
        ids = ",".join(str(x) for x in descartados[i:i + 200])
        db.atualizar("documentos_cvm", {"id": f"in.({ids})"},
                     {"status_processamento": "sem_indicio_risco"})
    print(f"Fila do LLM agora: {db.contar('documentos_cvm', {'status_processamento': 'eq.extraido'})}")


# ---------- etapa VETO ----------

def _json_da_resposta(texto: str) -> dict | None:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _normalizar_espacos(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _somar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    return date(d.year + m // 12, m % 12 + 1, 1)


def etapa_veto(piloto: int | None = None):
    cliente = gemini_client.GeminiClient()
    fila = db.selecionar("documentos_cvm",
                         {"status_processamento": "eq.extraido",
                          "select": "id,ticker,data_entrega,texto_extraido"},
                         order="id", limite=piloto)
    print(f"Documentos a classificar: {len(fila)}" + (f" (PILOTO de {piloto})" if piloto else ""))
    flags_gravadas, rejeitadas, sem_flag, erros_parse = 0, 0, 0, 0
    try:
        for i, doc in enumerate(fila, 1):
            resposta = cliente.gerar(PROMPT_VETO.replace("{TEXTO}", doc["texto_extraido"]))
            dados = _json_da_resposta(resposta["texto"])
            if dados is None:
                erros_parse += 1
                print(f"  [{i}] doc {doc['id']}: resposta não-JSON — pulado (fica 'extraido' p/ retentar)")
                continue
            if dados.get("flag_grave"):
                citacao = (dados.get("citacao_literal") or "")[:300]
                valida = bool(citacao) and _normalizar_espacos(citacao) in _normalizar_espacos(doc["texto_extraido"])
                mes_evento = datetime.strptime(doc["data_entrega"], "%Y-%m-%d").date().replace(day=1)
                flag = {
                    "ticker": doc["ticker"],
                    "doc_id": doc["id"],
                    "tipo_risco": dados.get("tipo_risco"),
                    "citacao_literal": citacao or None,
                    "validacao_literal": valida,
                    "mes_evento": mes_evento.isoformat(),
                    "veto_de": mes_evento.isoformat() if valida else None,
                    "veto_ate": _somar_meses(mes_evento, config.VETO_MESES).isoformat() if valida else None,
                    "modelo": config.GEMINI_MODEL_PINNED,
                    "model_version": resposta["model_version"],
                    "prompt_hash": PROMPT_VETO_HASH,
                }
                db.upsert("flags_relatorio", [flag], on_conflict="doc_id")
                if valida:
                    flags_gravadas += 1
                    print(f"  [{i}] VETO {doc['ticker']} {dados.get('tipo_risco')} ({mes_evento})")
                else:
                    rejeitadas += 1
                    print(f"  [{i}] flag REJEITADA (citação não literal) doc {doc['id']} {doc['ticker']}")
            else:
                sem_flag += 1
            db.atualizar("documentos_cvm", {"id": f"eq.{doc['id']}"}, {"status_processamento": "classificado"})
            if i % 50 == 0:
                print(f"  ... {i}/{len(fila)} (vetos={flags_gravadas} rejeitadas={rejeitadas})")
    except gemini_client.CotaEsgotadaError:
        print("\n!! Cota Gemini esgotada — parada limpa. Reexecute depois: continua de onde parou.")
    except gemini_client.ServicoIndisponivelError as e:
        print(f"\n!! Gemini indisponível ({e}) — parada limpa. Reexecute mais tarde.")
    print(f"\nResumo: {flags_gravadas} vetos válidos | {rejeitadas} flags rejeitadas (não literais) | "
          f"{sem_flag} sem risco grave | {erros_parse} erros de parse")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("etapa", choices=["metadados", "pdfs", "prefiltro", "veto"])
    parser.add_argument("--piloto", type=int, default=None, help="(veto) classifica só N documentos")
    parser.add_argument("--retentar", action="store_true", help="(pdfs) reprocessa também erro_download")
    args = parser.parse_args()
    if args.etapa == "metadados":
        etapa_metadados()
    elif args.etapa == "pdfs":
        etapa_pdfs(retentar_erros=args.retentar)
    elif args.etapa == "prefiltro":
        etapa_prefiltro()
    else:
        etapa_veto(piloto=args.piloto)
