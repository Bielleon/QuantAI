"""Testes do motor do backtest com dados SINTÉTICOS.

Cada teste usa entradas em que a resposta certa é conhecida na mão, para provar
que o código implementa as regras travadas da seção 8 — e não só que "roda".
Rodar: python tests/test_backtest.py   (não precisa de banco, rede ou LLM)
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from utils import config  # noqa: E402

_spec = importlib.util.spec_from_file_location("bt", RAIZ / "scripts" / "08_backtest.py")
bt = importlib.util.module_from_spec(_spec)
sys.modules["bt"] = bt
_spec.loader.exec_module(bt)

FALHAS: list[str] = []


def checar(condicao: bool, descricao: str, detalhe: str = ""):
    if condicao:
        print(f"  [OK] {descricao}")
    else:
        FALHAS.append(f"{descricao} — {detalhe}")
        print(f"  [XX] {descricao}  {detalhe}")


def quase(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) < tol


# ---------- helpers para montar cenários ----------

def indicadores_de(mercado_s: float, empresas: dict[str, tuple[float, int, bool]]) -> dict:
    """empresas: ticker -> (S, n_noticias, vetado)"""
    ind = {("MERCADO", "2024-01"): {"escopo": "MERCADO", "mes": "2024-01", "s": mercado_s,
                                    "n_noticias": 100, "n_pos": 0, "n_neg": 0, "n_neu": 0,
                                    "vetado": False, "elegivel": False}}
    for t, (s, n, vet) in empresas.items():
        ind[(t, "2024-01")] = {"escopo": t, "mes": "2024-01", "s": s, "n_noticias": n,
                               "n_pos": 0, "n_neg": 0, "n_neu": 0, "vetado": vet,
                               "elegivel": n >= config.MIN_NOTICIAS_MES and not vet}
    return ind


def precos_de(tickers: list[str]) -> dict:
    """Série trivial: preço 100 em todos os dias úteis de 2023-10 a 2024-02."""
    dias = []
    for mes in ("2023-10", "2023-11", "2023-12", "2024-01", "2024-02"):
        for d in range(1, 29):
            dias.append(f"{mes}-{d:02d}")
    return {t: {d: 100.0 for d in dias} for t in tickers + ["^BVSP"]}


def pesos_da_carteira(carteira: dict) -> dict[str, float]:
    return {p["ticker"]: p["peso"] for p in carteira["posicoes"] if p["peso"] > 0}


# ---------- testes: alocação macro ----------

def teste_alocacao_macro():
    print("\n== Alocação macro: %RV = 60 -/+ 20*S, limitado a [40,80] ==")
    universo = ["AAAA3"]
    precos = precos_de(universo)
    limite = date(2024, 1, 31)
    casos = [
        # (S_mercado, variante, %RV esperado em fração)
        (0.0, "contraria", 0.60), (0.0, "invertida", 0.60),
        (0.5, "contraria", 0.50), (0.5, "invertida", 0.70),
        (-0.5, "contraria", 0.70), (-0.5, "invertida", 0.50),
        (1.0, "contraria", 0.40), (1.0, "invertida", 0.80),
        (-1.0, "contraria", 0.80), (-1.0, "invertida", 0.40),
    ]
    for s_merc, variante, esperado in casos:
        ind = indicadores_de(s_merc, {"AAAA3": (0.0, 20, False)})
        c = bt.montar_carteira(ind, universo, "2024-01", variante, precos, limite)
        checar(quase(c["pct_rv"], esperado),
               f"S={s_merc:+.1f} {variante} -> %RV={esperado*100:.0f}%",
               f"obtido {c['pct_rv']*100:.1f}%")
    # clamp: S extremo não pode passar dos limites
    ind = indicadores_de(-1.0, {"AAAA3": (0.0, 20, False)})
    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, limite)
    checar(c["pct_rv"] <= config.PCT_RV_MAX / 100 + 1e-9, "clamp superior respeitado (<=80%)",
           f"obtido {c['pct_rv']}")


# ---------- testes: pesos por rank ----------

def teste_pesos_por_rank():
    print("\n== Pesos por rank: peso ∝ (N+1−rank), soma 1, teto 15% ==")
    universo = ["AAAA3", "BBBB3", "CCCC3"]
    precos = precos_de(universo)
    limite = date(2024, 1, 31)
    # S: A=-0,5 (mais negativa), B=0,0, C=+0,5
    ind = indicadores_de(0.0, {"AAAA3": (-0.5, 20, False), "BBBB3": (0.0, 20, False),
                               "CCCC3": (0.5, 20, False)})

    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, limite)
    pesos = pesos_da_carteira(c)
    # N=3: pesos brutos 3,2,1 -> 0,5 / 0,333 / 0,167 (nenhum acima do teto de 15%? 50% > 15%!)
    # com teto de 15% e só 3 candidatas, TODAS batem no teto e a soma não fecha 1 —
    # o comportamento correto é distribuir igualmente respeitando o teto até onde der.
    checar(quase(sum(pesos.values()), 1.0, 1e-6), "soma dos pesos = 1",
           f"obtido {sum(pesos.values()):.6f}")
    checar(pesos["AAAA3"] >= pesos["BBBB3"] >= pesos["CCCC3"],
           "contrária: S mais NEGATIVA recebe o maior peso",
           f"A={pesos['AAAA3']:.3f} B={pesos['BBBB3']:.3f} C={pesos['CCCC3']:.3f}")

    c = bt.montar_carteira(ind, universo, "2024-01", "invertida", precos, limite)
    pesos_inv = pesos_da_carteira(c)
    checar(pesos_inv["CCCC3"] >= pesos_inv["BBBB3"] >= pesos_inv["AAAA3"],
           "invertida: S mais POSITIVA recebe o maior peso",
           f"A={pesos_inv['AAAA3']:.3f} B={pesos_inv['BBBB3']:.3f} C={pesos_inv['CCCC3']:.3f}")

    # proporção exata com N=20 (nenhum peso bate no teto? peso máx = 20/210 = 9,5%)
    universo20 = [f"T{i:02d}A3" for i in range(20)]
    precos20 = precos_de(universo20)
    emp = {t: (i * 0.05 - 0.5, 20, False) for i, t in enumerate(universo20)}
    ind20 = indicadores_de(0.0, emp)
    c20 = bt.montar_carteira(ind20, universo20, "2024-01", "contraria", precos20, limite)
    p20 = pesos_da_carteira(c20)
    esperado_maior = 20 / (20 * 21 / 2)  # 20/210 = 0,0952
    maior = max(p20.values())
    checar(quase(maior, esperado_maior, 1e-4),
           f"N=20: maior peso = 20/210 = {esperado_maior:.4f}", f"obtido {maior:.4f}")
    checar(quase(sum(p20.values()), 1.0, 1e-6), "N=20: soma = 1",
           f"obtido {sum(p20.values()):.6f}")
    checar(all(p <= config.TETO_PESO_ACAO + 1e-9 for p in p20.values()),
           "N=20: nenhum peso acima do teto de 15%")


def teste_teto_15pct():
    print("\n== Teto de 15% por ação ==")
    universo = ["AAAA3", "BBBB3", "CCCC3", "DDDD3", "EEEE3", "FFFF3", "GGGG3"]
    precos = precos_de(universo)
    ind = indicadores_de(0.0, {t: (i * 0.1 - 0.3, 20, False) for i, t in enumerate(universo)})
    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, date(2024, 1, 31))
    pesos = pesos_da_carteira(c)
    checar(all(p <= config.TETO_PESO_ACAO + 1e-9 for p in pesos.values()),
           "N=7: nenhum peso passa de 15%", f"máx={max(pesos.values()):.4f}")
    checar(quase(sum(pesos.values()), 1.0, 1e-6), "N=7: soma continua 1 após o teto",
           f"obtido {sum(pesos.values()):.6f}")


# ---------- testes: veto e elegibilidade ----------

def teste_veto_e_elegibilidade():
    print("\n== Veto manda mais que sentimento; elegibilidade >= 10 notícias ==")
    universo = ["AAAA3", "BBBB3", "CCCC3"]
    precos = precos_de(universo)
    # A: S ótima para a contrária (bem negativa) MAS vetada -> tem de ficar fora
    # B: elegível normal | C: só 9 notícias -> inelegível
    ind = indicadores_de(0.0, {"AAAA3": (-0.9, 50, True), "BBBB3": (0.0, 20, False),
                               "CCCC3": (-0.8, 9, False)})
    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, date(2024, 1, 31))
    por_ticker = {p["ticker"]: p for p in c["posicoes"]}
    checar(por_ticker["AAAA3"]["classificacao_final"] == "vetado" and por_ticker["AAAA3"]["peso"] == 0,
           "empresa vetada fica FORA mesmo com o melhor sentimento",
           str(por_ticker["AAAA3"]))
    checar(por_ticker["CCCC3"]["classificacao_final"] == "inelegivel_poucas_noticias"
           and por_ticker["CCCC3"]["peso"] == 0,
           "empresa com 9 notícias (< 10) fica FORA", str(por_ticker["CCCC3"]))
    checar(quase(por_ticker["BBBB3"]["peso"], 1.0),
           "sobrando 1 elegível, ela leva 100% da RV", f"obtido {por_ticker['BBBB3']['peso']}")
    checar(por_ticker["AAAA3"]["flags_relatorio"] == "veto_ativo",
           "posição vetada registra a flag para auditoria")


def teste_variaveis_de_auditoria():
    print("\n== Persistência auditável: variáveis SEPARADAS em cada posição ==")
    universo = ["AAAA3"]
    precos = precos_de(universo)
    ind = indicadores_de(0.2, {"AAAA3": (-0.3, 15, False)})
    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, date(2024, 1, 31))
    p = c["posicoes"][0]
    obrigatorias = ["ticker", "peso", "sentimento_noticias", "n_noticias", "flags_relatorio",
                    "tendencia_numerica", "classificacao_final"]
    faltando = [k for k in obrigatorias if k not in p]
    checar(not faltando, "posição grava todas as variáveis exigidas", f"faltam {faltando}")
    checar(p["sentimento_noticias"] == -0.3 and p["n_noticias"] == 15,
           "valores de sentimento e contagem preservados", str(p))


# ---------- testes: S e indicadores ----------

def teste_formula_S():
    print("\n== Fórmula S = (pos − neg) / (pos + neg + neu) ==")
    dados = {
        "sentimentos": (
            [{"noticia_id": i, "sentimento": "positivo", "ticker_mapeado": "AAAA3"} for i in range(1, 7)] +
            [{"noticia_id": i, "sentimento": "negativo", "ticker_mapeado": "AAAA3"} for i in range(7, 10)] +
            [{"noticia_id": i, "sentimento": "neutro", "ticker_mapeado": "AAAA3"} for i in range(10, 12)]
        ),
        "mes_da_noticia": {i: "2024-01" for i in range(1, 12)},
        "flags": [],
    }
    ind = bt.calcular_indicadores(dados)
    s = ind[("AAAA3", "2024-01")]["s"]
    esperado = (6 - 3) / 11
    checar(quase(s, round(esperado, 6)), f"S = (6−3)/11 = {esperado:.6f}", f"obtido {s}")
    checar(ind[("AAAA3", "2024-01")]["n_noticias"] == 11, "n_noticias = 11")
    s_merc = ind[("MERCADO", "2024-01")]["s"]
    checar(quase(s_merc, round(esperado, 6)),
           "MERCADO agrega as mesmas notícias quando só há 1 empresa", f"obtido {s_merc}")


def teste_mercado_inclui_nao_mapeadas():
    print("\n== MERCADO conta TODAS as notícias, inclusive as sem empresa ==")
    dados = {
        "sentimentos": [
            {"noticia_id": 1, "sentimento": "positivo", "ticker_mapeado": "AAAA3"},
            {"noticia_id": 2, "sentimento": "negativo", "ticker_mapeado": None},
            {"noticia_id": 3, "sentimento": "neutro", "ticker_mapeado": None},
        ],
        "mes_da_noticia": {1: "2024-01", 2: "2024-01", 3: "2024-01"},
        "flags": [],
    }
    ind = bt.calcular_indicadores(dados)
    checar(ind[("MERCADO", "2024-01")]["n_noticias"] == 3,
           "MERCADO soma as 3 notícias", str(ind[("MERCADO", "2024-01")]["n_noticias"]))
    checar(ind[("AAAA3", "2024-01")]["n_noticias"] == 1,
           "empresa conta só a sua", str(ind[("AAAA3", "2024-01")]["n_noticias"]))


def teste_janela_de_veto():
    print("\n== Veto vale de mes_evento até mes_evento + 6 meses ==")
    dados = {
        "sentimentos": [{"noticia_id": i, "sentimento": "neutro", "ticker_mapeado": "AAAA3"}
                        for i in range(1, 25)],
        "mes_da_noticia": {i: f"2024-{((i - 1) % 12) + 1:02d}" for i in range(1, 25)},
        "flags": [{"ticker": "AAAA3", "mes_evento": "2024-03-01",
                   "veto_de": "2024-03-01", "veto_ate": "2024-09-01", "tipo_risco": "x"}],
    }
    ind = bt.calcular_indicadores(dados)
    checar(ind[("AAAA3", "2024-02")]["vetado"] is False, "fev/2024 (antes) NÃO vetado")
    checar(ind[("AAAA3", "2024-03")]["vetado"] is True, "mar/2024 (evento) vetado")
    checar(ind[("AAAA3", "2024-08")]["vetado"] is True, "ago/2024 (dentro dos 6 meses) vetado")
    checar(ind[("AAAA3", "2024-09")]["vetado"] is False, "set/2024 (fim da janela) NÃO vetado")


# ---------- testes: métricas ----------

def teste_metricas_conhecidas():
    print("\n== Métricas em série sintética de resposta conhecida ==")
    # 12 meses, +1% ao mês exato => retorno total = 1,01^12 − 1 = 12,68%
    serie = []
    v = 1.0
    for m in range(1, 14):
        serie.append({"data": f"2024-{m:02d}-02" if m <= 12 else "2025-01-02", "patrimonio": v})
        v *= 1.01
    taxa_zero = {}
    m = bt.metricas(serie, taxa_zero)
    esperado_total = 1.01 ** 12 - 1
    checar(quase(m["retorno_total"], esperado_total, 1e-6),
           f"retorno total = 1,01^12 − 1 = {esperado_total*100:.2f}%",
           f"obtido {m['retorno_total']*100:.4f}%")
    checar(quase(m["vol_anual"], 0.0, 1e-9), "volatilidade de série constante = 0",
           f"obtido {m['vol_anual']}")
    checar(quase(m["max_drawdown"], 0.0, 1e-9), "drawdown de série sempre subindo = 0",
           f"obtido {m['max_drawdown']}")
    checar(m["cagr"] > 0.12 and m["cagr"] < 0.13, "CAGR ~12,7% ao ano", f"obtido {m['cagr']:.4f}")

    # drawdown conhecido: 1,00 -> 1,20 -> 0,90 => DD = 0,90/1,20 − 1 = −25%
    serie_dd = [{"data": "2024-01-02", "patrimonio": 1.0},
                {"data": "2024-02-01", "patrimonio": 1.2},
                {"data": "2024-03-01", "patrimonio": 0.9}]
    m2 = bt.metricas(serie_dd, {})
    checar(quase(m2["max_drawdown"], 0.9 / 1.2 - 1, 1e-9),
           "máximo drawdown = −25%", f"obtido {m2['max_drawdown']*100:.2f}%")


def teste_selic_acumulada():
    print("\n== Selic acumulada: intervalo (de, ate], sem contar duas vezes ==")
    taxa = {"2024-01-01": 0.01, "2024-01-02": 0.01, "2024-01-03": 0.01}
    f = bt.acumular_selic(taxa, "2024-01-01", "2024-01-03")
    checar(quase(f, 1.01 * 1.01, 1e-9),
           "de 01 a 03 acumula 2 dias (exclui o inicial, inclui o final)", f"obtido {f}")
    checar(quase(bt.acumular_selic(taxa, "2024-01-03", "2024-01-03"), 1.0, 1e-12),
           "intervalo vazio = fator 1")


def teste_forward_fill_nao_ve_futuro():
    print("\n== preco_em: forward-fill nunca usa preço futuro ==")
    precos = {"AAAA3": {"2024-01-05": 100.0, "2024-01-10": 200.0}}
    checar(bt.preco_em(precos, "AAAA3", "2024-01-07") == 100.0,
           "dia sem cotação usa o ÚLTIMO anterior (100), não o futuro (200)",
           f"obtido {bt.preco_em(precos, 'AAAA3', '2024-01-07')}")
    checar(bt.preco_em(precos, "AAAA3", "2024-01-01") is None,
           "antes do início da série devolve None (não inventa preço)")


def teste_tendencia_nao_decide():
    print("\n== Tendência numérica é gravada mas NÃO altera a decisão ==")
    universo = ["AAAA3", "BBBB3"]
    # preços diferentes: A subindo forte, B caindo — não pode mudar os pesos
    dias = [f"2023-{m:02d}-{d:02d}" for m in (10, 11, 12) for d in range(1, 29)]
    dias += [f"2024-01-{d:02d}" for d in range(1, 29)]
    precos = {"AAAA3": {d: 100.0 + i for i, d in enumerate(dias)},
              "BBBB3": {d: 200.0 - i for i, d in enumerate(dias)},
              "^BVSP": {d: 100.0 for d in dias}}
    ind = indicadores_de(0.0, {"AAAA3": (-0.5, 20, False), "BBBB3": (-0.5, 20, False)})
    c = bt.montar_carteira(ind, universo, "2024-01", "contraria", precos, date(2024, 1, 28))
    pesos = pesos_da_carteira(c)
    checar(quase(pesos["AAAA3"], pesos["BBBB3"], 1e-9),
           "mesmo S => mesmo peso, apesar de tendências opostas",
           f"A={pesos['AAAA3']:.4f} B={pesos['BBBB3']:.4f}")
    tend = {p["ticker"]: p["tendencia_numerica"] for p in c["posicoes"]}
    checar(tend["AAAA3"] is not None and tend["BBBB3"] is not None,
           "tendência é calculada e gravada para auditoria", str(tend))
    checar(tend["AAAA3"] > 0 > tend["BBBB3"],
           "sinal da tendência bate com o movimento dos preços", str(tend))


def main():
    print("=== Testes do motor do backtest (dados sintéticos) ===")
    teste_alocacao_macro()
    teste_pesos_por_rank()
    teste_teto_15pct()
    teste_veto_e_elegibilidade()
    teste_variaveis_de_auditoria()
    teste_formula_S()
    teste_mercado_inclui_nao_mapeadas()
    teste_janela_de_veto()
    teste_metricas_conhecidas()
    teste_selic_acumulada()
    teste_forward_fill_nao_ve_futuro()
    teste_tendencia_nao_decide()

    print("\n" + "=" * 60)
    if FALHAS:
        print(f"FALHOU: {len(FALHAS)} verificação(ões)")
        for f in FALHAS:
            print(f"  - {f}")
        sys.exit(1)
    print("TODOS OS TESTES PASSARAM")


if __name__ == "__main__":
    main()
