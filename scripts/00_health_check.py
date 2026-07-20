"""FASE 0 — Health check de TODAS as chaves e fontes de dados.

Faz 1 chamada mínima em cada chave (Supabase x2, Apify x7, Gemini x3) e testa
as fontes sem chave (BCB/Selic, CVM, yfinance). Imprime uma tabela de status
com as chaves SEMPRE mascaradas. Na 1ª chamada Gemini bem-sucedida, grava o
modelVersion em GEMINI_MODEL_PINNED no .env (se ainda estiver vazio).
Custo: ~zero (chamadas mínimas dentro das cotas gratuitas).
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import apify_client, config, gemini_client  # noqa: E402

RESULTADOS: list[tuple[str, str, str, str]] = []  # (serviço, chave mascarada, status, detalhe)


def registrar(servico: str, chave: str, ok: bool, detalhe: str = ""):
    # defesa em profundidade: SEMPRE redigir ANTES de truncar (truncar primeiro
    # poderia cortar uma chave no meio e impedir o mascaramento de reconhecê-la)
    detalhe = config.redigir(detalhe)[:200]
    RESULTADOS.append((servico, chave, "OK" if ok else "FALHOU", detalhe))
    print(f"  [{'OK' if ok else 'XX'}] {servico:<22} {chave:<22} {detalhe}")


def testar_presenca_no_env():
    """Garante que .env vazio/mal digitado NÃO passa em silêncio (0 chaves != tudo OK)."""
    esperado = [
        ("SUPABASE_SERVICE_ROLE_KEY", bool(config.SUPABASE_SERVICE_ROLE_KEY)),
        ("SUPABASE_ANON_KEY", bool(config.SUPABASE_ANON_KEY)),
        ("APIFY_TOKENS (>=1)", len(config.APIFY_TOKENS) >= 1),
        ("GEMINI_API_KEYS (>=1)", len(config.GEMINI_API_KEYS) >= 1),
    ]
    for nome, presente in esperado:
        if not presente:
            registrar(f"presença no .env: {nome}", "-", False, "variável ausente ou vazia no .env")


def testar_supabase():
    for nome, chave in [("supabase service_role", config.SUPABASE_SERVICE_ROLE_KEY),
                        ("supabase anon", config.SUPABASE_ANON_KEY)]:
        try:
            r = requests.get(
                f"{config.SUPABASE_URL}/rest/v1/",
                headers={"apikey": chave, "Authorization": f"Bearer {chave}"},
                timeout=30,
            )
            registrar(nome, config.mascarar(chave), r.status_code == 200, f"HTTP {r.status_code}")
        except requests.RequestException as e:
            registrar(nome, config.mascarar(chave), False, str(e))


def testar_apify():
    for i, token in enumerate(config.APIFY_TOKENS, 1):
        res = apify_client.testar_token(token)
        detalhe = f"user={res.get('username')} plano={res.get('plano')}" if res["ok"] else res["detalhe"]
        registrar(f"apify token {i}/7", config.mascarar(token), res["ok"], detalhe)


def testar_gemini() -> dict[str, str]:
    """Testa cada chave; devolve mapa chave->endpoint e pina o modelo na 1ª que funcionar."""
    endpoints: dict[str, str] = {}
    for i, chave in enumerate(config.GEMINI_API_KEYS, 1):
        res = gemini_client.testar_chave(chave)
        if res["ok"]:
            endpoints[chave] = res["endpoint"]
            detalhe = f"endpoint={res['endpoint']} modelo={res['modelo_pedido']} versao={res['model_version']}"
            if not config.GEMINI_MODEL_PINNED and res["model_version"]:
                config.gravar_model_pinned(res["model_version"])
                detalhe += "  << PINADO no .env"
        else:
            detalhe = res["detalhe"]
        registrar(f"gemini chave {i}/3", config.mascarar(chave), res["ok"], detalhe)
    return endpoints


def testar_fontes_sem_chave():
    # BCB / Selic (série SGS 11)
    try:
        r = requests.get(
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados/ultimos/1",
            params={"formato": "json"}, timeout=30,
        )
        dado = r.json()[0] if r.status_code == 200 else {}
        registrar("BCB Selic (SGS 11)", "-", r.status_code == 200,
                  f"último dado: {dado.get('data')} = {dado.get('valor')}")
    except Exception as e:  # noqa: BLE001
        registrar("BCB Selic (SGS 11)", "-", False, str(e))

    # CVM IPE (dados abertos)
    try:
        r = requests.get(
            "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_2021.zip",
            stream=True, timeout=30,
        )
        tamanho = r.headers.get("Content-Length", "?")
        r.close()
        registrar("CVM IPE (zip 2021)", "-", r.status_code == 200, f"HTTP {r.status_code}, {tamanho} bytes")
    except Exception as e:  # noqa: BLE001
        registrar("CVM IPE (zip 2021)", "-", False, str(e))

    # yfinance (^BVSP, 5 dias)
    try:
        import yfinance as yf
        hist = yf.Ticker("^BVSP").history(period="5d")
        registrar("yfinance (^BVSP)", "-", not hist.empty, f"{len(hist)} pregões retornados")
    except Exception as e:  # noqa: BLE001
        registrar("yfinance (^BVSP)", "-", False, str(e))


def main():
    print("=== QuantAI — Health check (FASE 0) ===\n")
    testar_presenca_no_env()
    testar_supabase()
    testar_apify()
    testar_gemini()
    testar_fontes_sem_chave()

    print("\n=== Tabela final ===")
    print(f"{'Serviço':<24} | {'Chave':<22} | {'Status':<7} | Detalhe")
    print("-" * 110)
    for servico, chave, status, detalhe in RESULTADOS:
        print(f"{servico:<24} | {chave:<22} | {status:<7} | {detalhe[:60]}")

    falhas = [r for r in RESULTADOS if r[2] == "FALHOU"]
    print(f"\nTotal: {len(RESULTADOS)} testes, {len(falhas)} falha(s).")
    print(f"GEMINI_MODEL_PINNED = {config.GEMINI_MODEL_PINNED or '(NÃO PINADO — verificar chaves!)'}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
