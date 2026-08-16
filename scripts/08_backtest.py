"""FASE 8 — Indicadores, backtest (2 variantes) e saídas finais.

Implementação PRÓPRIA (exigência do desafio: nunca delegar a plataforma pronta).

Pipeline:
  1. indicadores: S mensal por empresa e do MERCADO, elegibilidade (>=10 notícias),
     vetos da CVM (6 meses), tendência numérica (retorno de ~63 pregões, gravada
     para auditoria — NÃO entra na decisão do robô base).
  2. backtest: para cada mês M, sinais usam SOMENTE dados até o último dia de M;
     execução no fechamento ajustado do 1º pregão de M+1.
       %RV = 60 -/+ 20*S_mercado, limitado a [40,80]
       pesos por rank de S (mais negativa = rank 1 na variante contrária),
       peso ∝ (N+1−rank), teto de 15%/ação, renormalização após teto e vetos
       custo de 0,1% sobre o turnover; perna RF acumula Selic diária
  3. benchmarks: Ibovespa, Selic pura e 60/40 rebalanceado mensalmente
  4. métricas, gráficos, JSON mensal em carteiras.posicoes, cartas do KRON
  5. teste automatizado anti-look-ahead (assert)

Uso: 08_backtest.py [--sem-cartas]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import config, db  # noqa: E402

NOME_ROBO = "KRON"  # Kernel de Rastreamento de Otimismo e Notícias (definido pela equipe)
VARIANTES = ("contraria", "invertida")


# ---------------- utilidades de calendário ----------------

def primeiro_dia(mes: str) -> date:
    return datetime.strptime(mes + "-01", "%Y-%m-%d").date()


def mes_seguinte(mes: str) -> str:
    d = primeiro_dia(mes)
    return f"{d.year + d.month // 12}-{d.month % 12 + 1:02d}"


def meses_do_backtest() -> list[str]:
    saida, atual = [], config.BACKTEST_INICIO
    while atual <= config.BACKTEST_FIM:
        saida.append(atual)
        atual = mes_seguinte(atual)
    return saida


# ---------------- carga de dados ----------------

def carregar_tudo() -> dict:
    print("Carregando dados do banco...")
    universo = db.selecionar("universo", {"ativo": "eq.true", "select": "ticker,nome,setor"},
                             order="ticker")
    precos_lin = db.selecionar("precos", {"select": "ticker,data,fech_ajustado"}, order="ticker,data")
    taxa = db.selecionar("taxa_rf", {"select": "data,taxa_diaria"}, order="data")
    noticias = db.selecionar("noticias", {"select": "id,data_pub", "titulo": "not.is.null"}, order="id")
    sentimentos = db.selecionar("sentimentos", {"select": "noticia_id,sentimento,ticker_mapeado"},
                                order="noticia_id")
    flags = db.selecionar("flags_relatorio", {"select": "ticker,mes_evento,veto_de,veto_ate,tipo_risco",
                                              "validacao_literal": "is.true"}, order="id")

    precos: dict[str, dict[str, float]] = defaultdict(dict)
    for p in precos_lin:
        precos[p["ticker"]][p["data"]] = p["fech_ajustado"]
    data_por_noticia = {n["id"]: n["data_pub"][:7] for n in noticias}

    print(f"  universo={len(universo)} | precos={len(precos_lin)} | selic={len(taxa)} | "
          f"noticias={len(noticias)} | sentimentos={len(sentimentos)} | vetos={len(flags)}")
    return {"universo": [u["ticker"] for u in universo], "precos": dict(precos),
            "taxa": {t["data"]: t["taxa_diaria"] for t in taxa},
            "mes_da_noticia": data_por_noticia, "sentimentos": sentimentos, "flags": flags}


# ---------------- 1. indicadores ----------------

def calcular_indicadores(dados: dict) -> dict:
    """S mensal por empresa e MERCADO + elegibilidade + veto. Só código, sem LLM."""
    cont: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})
    for s in dados["sentimentos"]:
        mes = dados["mes_da_noticia"].get(s["noticia_id"])
        if not mes:
            continue
        chave_sent = {"positivo": "pos", "negativo": "neg", "neutro": "neu"}[s["sentimento"]]
        cont[("MERCADO", mes)][chave_sent] += 1
        if s["ticker_mapeado"]:
            cont[(s["ticker_mapeado"], mes)][chave_sent] += 1

    vetos: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in dados["flags"]:
        if f["veto_de"] and f["veto_ate"]:
            vetos[f["ticker"]].append((f["veto_de"][:7], f["veto_ate"][:7]))

    indicadores = {}
    for (escopo, mes), c in cont.items():
        n = c["pos"] + c["neg"] + c["neu"]
        s = (c["pos"] - c["neg"]) / n if n else 0.0
        vetado = any(de <= mes < ate for de, ate in vetos.get(escopo, []))
        indicadores[(escopo, mes)] = {
            "escopo": escopo, "mes": mes, "n_noticias": n,
            "n_pos": c["pos"], "n_neg": c["neg"], "n_neu": c["neu"], "s": round(s, 6),
            "vetado": vetado,
            "elegivel": escopo != "MERCADO" and n >= config.MIN_NOTICIAS_MES and not vetado,
        }
    return indicadores


def tendencia_numerica(precos: dict, ticker: str, ate: date) -> float | None:
    """Retorno acumulado dos ~63 últimos pregões até `ate` (auditoria; não decide nada
    no robô base). Calculado por CÓDIGO — nenhum número passa pela LLM."""
    serie = sorted((d, v) for d, v in precos.get(ticker, {}).items() if d <= ate.isoformat())
    if len(serie) < 64:
        return None
    return round(serie[-1][1] / serie[-64][1] - 1, 6)


# ---------------- 2. motor do backtest ----------------

def pregoes_ate(precos: dict, ticker: str, limite: str) -> list[tuple[str, float]]:
    return sorted((d, v) for d, v in precos.get(ticker, {}).items() if d <= limite)


def primeiro_pregao_de(precos: dict, mes: str) -> str | None:
    """1º pregão do mês pelo calendário do ^BVSP (referência da B3)."""
    dias = sorted(d for d in precos.get("^BVSP", {}) if d[:7] == mes)
    return dias[0] if dias else None


def preco_em(precos: dict, ticker: str, dia: str) -> float | None:
    """Preço no dia; se faltar (2 pregões ausentes na fonte), repete o último anterior."""
    serie = precos.get(ticker, {})
    if dia in serie:
        return serie[dia]
    anteriores = [d for d in serie if d <= dia]
    return serie[max(anteriores)] if anteriores else None


def acumular_selic(taxa: dict, de: str, ate: str) -> float:
    """Fator acumulado da Selic diária no intervalo (de, ate]."""
    fator = 1.0
    for d, t in taxa.items():
        if de < d <= ate:
            fator *= (1 + t)
    return fator


def quase_igual(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


def montar_carteira(indicadores: dict, universo: list[str], mes: str, variante: str,
                    precos: dict, limite_dados: date) -> dict:
    """Decide a carteira do mês M usando SOMENTE dados até o fim de M."""
    ind_mercado = indicadores.get(("MERCADO", mes))
    s_mercado = ind_mercado["s"] if ind_mercado else 0.0
    sinal = -1 if variante == "contraria" else +1
    pct_rv = config.PCT_RV_BASE + sinal * config.PCT_RV_SENSIBILIDADE * s_mercado
    pct_rv = max(config.PCT_RV_MIN, min(config.PCT_RV_MAX, pct_rv)) / 100.0

    posicoes = []
    candidatas = []
    for ticker in universo:
        ind = indicadores.get((ticker, mes))
        n = ind["n_noticias"] if ind else 0
        s = ind["s"] if ind else 0.0
        vetado = ind["vetado"] if ind else False
        tem_preco = preco_em(precos, ticker, limite_dados.isoformat()) is not None
        base = {"ticker": ticker, "sentimento_noticias": s, "n_noticias": n,
                "flags_relatorio": "veto_ativo" if vetado else None,
                "tendencia_numerica": tendencia_numerica(precos, ticker, limite_dados),
                "peso": 0.0}
        if vetado:
            posicoes.append({**base, "classificacao_final": "vetado"})
        elif n < config.MIN_NOTICIAS_MES or not tem_preco:
            posicoes.append({**base, "classificacao_final": "inelegivel_poucas_noticias"})
        else:
            candidatas.append(base)

    if candidatas:
        # rank 1 = S mais NEGATIVA na contrária (aposta contra o humor);
        #          S mais POSITIVA na invertida (momentum).
        # Desempate por ticker: sem isso a ordem viria do banco e o resultado não
        # seria replicável.
        candidatas.sort(key=lambda c: (c["sentimento_noticias"] * (1 if variante == "contraria" else -1),
                                       c["ticker"]))
        n_cand = len(candidatas)
        pesos = [n_cand + 1 - (i + 1) for i in range(n_cand)]
        # EMPATES: empresas com o MESMO S dividem a média dos pesos do bloco.
        # A regra travada não define desempate; sem isso, duas empresas idênticas
        # receberiam pesos muito diferentes (ex.: 2/3 vs 1/3) só pela ordem de leitura.
        i = 0
        while i < n_cand:
            j = i
            while j + 1 < n_cand and quase_igual(candidatas[j + 1]["sentimento_noticias"],
                                                 candidatas[i]["sentimento_noticias"]):
                j += 1
            if j > i:
                media = sum(pesos[i:j + 1]) / (j - i + 1)
                for k in range(i, j + 1):
                    pesos[k] = media
            i = j + 1
        total = sum(pesos)
        pesos = [p / total for p in pesos]
        # Teto de 15% com redistribuição do excedente para as não-tetadas.
        # O corte min(p, TETO) vem ANTES de qualquer saída do laço: com N <= 6 é
        # matematicamente impossível todos ficarem <= 15% E somarem 1 (6×15% = 90%),
        # e a versão anterior escapava do laço sem aplicar o teto — deixando, por
        # exemplo, 100% da RV numa única ação. Quando o teto trava para todas, a
        # soma fica < 1 de propósito: a sobra da RV vai para o Tesouro Selic
        # (mecanismo já aprovado no item 44). Ver DECISOES.md item 50.
        for _ in range(100):
            excedente = sum(p - config.TETO_PESO_ACAO for p in pesos if p > config.TETO_PESO_ACAO)
            if excedente <= 1e-12:
                break
            pesos = [min(p, config.TETO_PESO_ACAO) for p in pesos]
            livres = [i for i, p in enumerate(pesos) if p < config.TETO_PESO_ACAO - 1e-12]
            if not livres:
                break  # teto trava todas: soma < 1, sobra vai para a renda fixa
            soma_livres = sum(pesos[i] for i in livres)
            if soma_livres <= 1e-12:
                for i in livres:
                    pesos[i] += excedente / len(livres)
            else:
                for i in livres:
                    pesos[i] += excedente * pesos[i] / soma_livres
        for c, p in zip(candidatas, pesos):
            c["peso"] = round(p, 6)
            c["classificacao_final"] = "carteira" if p > 1e-9 else "fora_por_rank"
            posicoes.append(c)

    posicoes.sort(key=lambda p: (-p["peso"], p["ticker"]))
    return {"mes": mes, "variante": variante, "pct_rv": round(pct_rv, 6),
            "s_mercado": round(s_mercado, 6), "posicoes": posicoes}


def rodar_backtest(dados: dict, indicadores: dict, variante: str) -> dict:
    """Devolve série de patrimônio + carteiras + turnover. Custo de 0,1% no turnover.

    CONVENÇÃO DA SÉRIE (igual à de benchmarks(), para que a comparação seja válida):
    o ponto na data d_i contém o patrimônio JÁ com tudo que aconteceu ATÉ d_i.
    O primeiro ponto vale 1,0 na primeira data de execução, ANTES de qualquer
    custo — assim o custo e o retorno do 1º rebalanceamento entram nas métricas.
    A versão anterior gravava em d_i o patrimônio que só existia em d_(i+1),
    adiantando a curva do robô em um mês frente aos benchmarks (DECISOES.md item 51).
    """
    precos, taxa = dados["precos"], dados["taxa"]
    universo = dados["universo"]
    meses = meses_do_backtest()
    datas_exec = [d for d in (primeiro_pregao_de(precos, mes_seguinte(m)) for m in meses) if d]
    if not datas_exec:
        return {"serie": [], "carteiras": [], "alocacao": [], "turnover_medio": 0.0}

    patrimonio = 1.0
    pesos_carregados: dict[str, float] = {}   # pesos REAIS na virada (já com drift de preço)
    peso_rf_carregado = 0.0
    serie = [{"data": datas_exec[0], "patrimonio": 1.0}]
    carteiras, turnovers, alocacao = [], [], []

    for i, mes in enumerate(meses):
        mes_exec = mes_seguinte(mes)
        dia_exec = primeiro_pregao_de(precos, mes_exec)
        if not dia_exec:
            continue
        # ANTI-LOOK-AHEAD: sinais só com dados até o último dia do mês M
        limite_dados = primeiro_dia(mes_exec) - timedelta(days=1)
        carteira = montar_carteira(indicadores, universo, mes, variante, precos, limite_dados)
        carteiras.append(carteira)

        pesos_novos = {p["ticker"]: p["peso"] * carteira["pct_rv"]
                       for p in carteira["posicoes"] if p["peso"] > 0}
        # A parcela de RV sem ação elegível (ou barrada pelo teto) vai para o Tesouro
        # Selic — não fica como dinheiro parado (DECISOES.md item 44).
        rv_investido = sum(pesos_novos.values())
        peso_rf = 1.0 - rv_investido
        carteira["pct_rv_efetivo"] = round(rv_investido, 6)

        # Turnover contra a carteira REALMENTE carregada (com drift de preço), não
        # contra o alvo do mês anterior — senão o custo do rebalanceamento é
        # subestimado (DECISOES.md item 52).
        tickers_uniao = set(pesos_novos) | set(pesos_carregados)
        turnover = sum(abs(pesos_novos.get(t, 0) - pesos_carregados.get(t, 0)) for t in tickers_uniao)
        turnover += abs(peso_rf - peso_rf_carregado)
        turnover /= 2
        turnovers.append(turnover)
        patrimonio *= (1 - config.CUSTO_TURNOVER * turnover)

        alocacao.append({"data": dia_exec, "mes_sinal": mes, "pct_rv": carteira["pct_rv"],
                         "pct_rv_efetivo": rv_investido, "n_acoes": len(pesos_novos)})

        # rendimento do período até a próxima execução
        dia_saida = datas_exec[i + 1] if i + 1 < len(datas_exec) else None
        if not dia_saida or dia_saida <= dia_exec:
            break
        retorno_rv = 0.0
        pesos_derivados: dict[str, float] = {}
        for ticker, peso in pesos_novos.items():
            p0, p1 = preco_em(precos, ticker, dia_exec), preco_em(precos, ticker, dia_saida)
            fator = (p1 / p0) if (p0 and p1) else 1.0
            retorno_rv += peso * (fator - 1)
            pesos_derivados[ticker] = peso * fator
        fator_rf = acumular_selic(taxa, dia_exec, dia_saida)
        retorno_rf = peso_rf * (fator_rf - 1)
        retorno_total = retorno_rv + retorno_rf
        patrimonio *= (1 + retorno_total)
        serie.append({"data": dia_saida, "patrimonio": patrimonio})

        # normaliza os pesos derivados para o novo patrimônio (drift)
        base = 1 + retorno_total
        pesos_carregados = {t: p / base for t, p in pesos_derivados.items()}
        peso_rf_carregado = (peso_rf * fator_rf) / base

    return {"serie": serie, "carteiras": carteiras, "alocacao": alocacao,
            "turnover_medio": sum(turnovers) / len(turnovers) if turnovers else 0.0}


# ---------------- 3. benchmarks ----------------

def benchmarks(dados: dict) -> dict[str, list[dict]]:
    precos, taxa = dados["precos"], dados["taxa"]
    meses = meses_do_backtest()
    datas = [primeiro_pregao_de(precos, mes_seguinte(m)) for m in meses]
    datas = [d for d in datas if d]

    ibov, selic, misto = [], [], []
    v_ibov = v_selic = v_misto = 1.0
    for i, dia in enumerate(datas):
        if i > 0:
            anterior = datas[i - 1]
            p0, p1 = preco_em(precos, "^BVSP", anterior), preco_em(precos, "^BVSP", dia)
            r_ibov = (p1 / p0 - 1) if (p0 and p1) else 0.0
            r_selic = acumular_selic(taxa, anterior, dia) - 1
            v_ibov *= (1 + r_ibov)
            v_selic *= (1 + r_selic)
            v_misto *= (1 + 0.6 * r_ibov + 0.4 * r_selic)  # 60/40 rebalanceado no mês
        ibov.append({"data": dia, "patrimonio": v_ibov})
        selic.append({"data": dia, "patrimonio": v_selic})
        misto.append({"data": dia, "patrimonio": v_misto})
    return {"ibovespa": ibov, "selic": selic, "60_40": misto}


# ---------------- 4. métricas ----------------

def metricas(serie: list[dict], taxa: dict, turnover_medio: float = 0.0) -> dict:
    if len(serie) < 2:
        return {}
    valores = [p["patrimonio"] for p in serie]
    datas = [p["data"] for p in serie]
    retornos = [valores[i] / valores[i - 1] - 1 for i in range(1, len(valores))]

    dias = (datetime.strptime(datas[-1], "%Y-%m-%d") - datetime.strptime(datas[0], "%Y-%m-%d")).days
    anos = dias / 365.25
    retorno_total = valores[-1] / valores[0] - 1
    cagr = (valores[-1] / valores[0]) ** (1 / anos) - 1 if anos > 0 else 0.0

    media = sum(retornos) / len(retornos)
    var = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1) if len(retornos) > 1 else 0.0
    vol_anual = (var ** 0.5) * (12 ** 0.5)

    # Sharpe com a MESMA série Selic usada na perna RF e no benchmark (consistência)
    excessos = []
    for i in range(1, len(datas)):
        rf = acumular_selic(taxa, datas[i - 1], datas[i]) - 1
        excessos.append(retornos[i - 1] - rf)
    media_ex = sum(excessos) / len(excessos)
    var_ex = sum((r - media_ex) ** 2 for r in excessos) / (len(excessos) - 1) if len(excessos) > 1 else 0.0
    sharpe = (media_ex * 12) / ((var_ex ** 0.5) * (12 ** 0.5)) if var_ex > 0 else 0.0

    pico, dd_max = valores[0], 0.0
    for v in valores:
        pico = max(pico, v)
        dd_max = min(dd_max, v / pico - 1)

    # o retorno do intervalo (datas[i-1], datas[i]] pertence ao mês/ano de datas[i-1]:
    # o período que começa em 01/12/2021 e vai até 03/01/2022 é o retorno de DEZEMBRO
    # de 2021 (antes era creditado a 2022 — DECISOES.md item 53)
    por_ano: dict[str, float] = {}
    for i in range(1, len(datas)):
        ano = datas[i - 1][:4]
        por_ano[ano] = (1 + por_ano.get(ano, 0.0)) * (1 + retornos[i - 1]) - 1

    return {"retorno_total": retorno_total, "cagr": cagr, "vol_anual": vol_anual,
            "sharpe": sharpe, "max_drawdown": dd_max, "turnover_medio": turnover_medio,
            "por_ano": {k: round(v, 4) for k, v in sorted(por_ano.items())},
            "meses": len(serie)}


# ---------------- 5. teste anti-look-ahead ----------------

def _recontar_noticias(dados: dict) -> dict[tuple[str, str], int]:
    """Recontagem INDEPENDENTE (ticker, mês) direto dos dados brutos."""
    contagem: dict[tuple[str, str], int] = defaultdict(int)
    for s in dados["sentimentos"]:
        mes = dados["mes_da_noticia"].get(s["noticia_id"])
        if mes and s["ticker_mapeado"]:
            contagem[(s["ticker_mapeado"], mes)] += 1
    return contagem


def _violacoes_look_ahead(dados: dict, carteiras: list[dict]) -> list[str]:
    """Compara o n_noticias usado em cada carteira com a recontagem independente
    restrita ao PRÓPRIO mês do sinal. Se o sinal do mês M tiver contado qualquer
    notícia de outro mês, os números divergem e a violação aparece aqui."""
    esperado = _recontar_noticias(dados)
    falhas = []
    for c in carteiras:
        mes = c["mes"]
        mes_exec = mes_seguinte(mes)
        for p in c["posicoes"]:
            if p["n_noticias"] != esperado.get((p["ticker"], mes), 0):
                falhas.append(f"{p['ticker']} {mes}: usou {p['n_noticias']} notícias, "
                              f"mas o mês tem {esperado.get((p['ticker'], mes), 0)}")
        dia_exec = primeiro_pregao_de(dados["precos"], mes_exec)
        if dia_exec and dia_exec[:7] != mes_exec:
            falhas.append(f"{mes}: execução fora de M+1 ({dia_exec})")
    return falhas


def teste_anti_look_ahead(dados: dict, carteiras: list[dict], indicadores: dict,
                          universo: list[str], variante: str) -> None:
    """Garante que nenhum dado posterior ao fim do mês M entrou no sinal de M.

    O teste tem DUAS partes. A primeira compara o sinal usado com uma recontagem
    independente. A segunda é um teste de MUTAÇÃO: injeta um look-ahead artificial
    (desloca as notícias um mês para trás, de modo que o sinal de M passe a
    enxergar notícias de M+1) e exige que a checagem ACUSE. Sem essa segunda
    parte o teste poderia ser vacuamente verdadeiro — foi exatamente o que
    aconteceu com a versão anterior (DECISOES.md item 54).
    """
    print("\n=== Teste anti-look-ahead ===")
    falhas = _violacoes_look_ahead(dados, carteiras)
    assert not falhas, "LOOK-AHEAD DETECTADO:\n" + "\n".join(falhas[:10])
    print(f"  [OK] {len(carteiras)} carteiras: n_noticias bate com a recontagem do próprio mês; "
          f"execução sempre no 1º pregão de M+1")

    # --- mutação: o teste consegue mesmo detectar um look-ahead? ---
    meses_ordenados = meses_do_backtest()
    anterior = {m: meses_ordenados[i - 1] for i, m in enumerate(meses_ordenados) if i > 0}
    dados_mutados = dict(dados)
    dados_mutados["mes_da_noticia"] = {nid: anterior.get(mes, mes)
                                       for nid, mes in dados["mes_da_noticia"].items()}
    ind_mutados = calcular_indicadores(dados_mutados)
    carteiras_mutadas = []
    for mes in meses_ordenados:
        dia_exec = primeiro_pregao_de(dados["precos"], mes_seguinte(mes))
        if not dia_exec:
            continue
        limite = primeiro_dia(mes_seguinte(mes)) - timedelta(days=1)
        carteiras_mutadas.append(
            montar_carteira(ind_mutados, universo, mes, variante, dados["precos"], limite))
    detectou = _violacoes_look_ahead(dados, carteiras_mutadas)
    assert detectou, ("O teste anti-look-ahead NÃO detectou um look-ahead injetado de "
                      "propósito — ele é vacuamente verdadeiro e não prova nada.")
    print(f"  [OK] mutação: look-ahead injetado foi DETECTADO ({len(detectou)} violações) — "
          f"o teste tem poder de detecção")


# ---------------- 6. saídas ----------------

def gravar_indicadores(indicadores: dict) -> None:
    linhas = [{k: v for k, v in ind.items()} for ind in indicadores.values()]
    for i, l in enumerate(linhas):
        l["mes"] = l["mes"] + "-01"
    for i in range(0, len(linhas), 500):
        db.upsert("indicadores", linhas[i:i + 500], on_conflict="escopo,mes")
    print(f"  indicadores gravados: {len(linhas)}")


def gravar_carteiras(carteiras: list[dict]) -> None:
    linhas = [{"mes": c["mes"] + "-01", "variante": c["variante"], "pct_rv": c["pct_rv"],
               "s_mercado": c["s_mercado"], "posicoes": c["posicoes"]} for c in carteiras]
    for i in range(0, len(linhas), 100):
        db.upsert("carteiras", linhas[i:i + 100], on_conflict="mes,variante")
    print(f"  carteiras gravadas: {len(linhas)}")


def gerar_graficos(resultados: dict, bench: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    config.OUTPUTS_DIR.mkdir(exist_ok=True)
    # 1. curva de patrimônio vs benchmarks
    fig, ax = plt.subplots(figsize=(11, 6))
    for variante, cor in [("contraria", "#1f77b4"), ("invertida", "#ff7f0e")]:
        s = resultados[variante]["serie"]
        ax.plot([p["data"] for p in s], [p["patrimonio"] for p in s],
                label=f"KRON ({variante})", color=cor, linewidth=2)
    for nome, cor in [("ibovespa", "#7f7f7f"), ("selic", "#2ca02c"), ("60_40", "#9467bd")]:
        b = bench[nome]
        ax.plot([p["data"] for p in b], [p["patrimonio"] for p in b],
                label=nome.replace("_", "/").title(), color=cor, linestyle="--", linewidth=1.3)
    ax.set_title("Patrimônio acumulado — KRON vs benchmarks")
    ax.set_ylabel("Patrimônio (base 1,00)")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.tick_params(axis="x", rotation=45, labelsize=7)
    ax.xaxis.set_major_locator(plt.MaxNLocator(14))
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "curva_patrimonio.png", dpi=140)
    plt.close(fig)

    # 2. alocação RV x RF no tempo
    fig, ax = plt.subplots(figsize=(11, 4))
    s = resultados["contraria"]["alocacao"]
    datas = [p["data"] for p in s]
    rv = [p["pct_rv_efetivo"] * 100 for p in s]
    ax.fill_between(datas, 0, rv, label="Renda variável (investido)", color="#1f77b4", alpha=0.75)
    ax.fill_between(datas, rv, 100, label="Tesouro Selic", color="#2ca02c", alpha=0.6)
    ax.set_ylim(0, 100)
    ax.set_title("Alocação da variante contrária ao longo do tempo")
    ax.set_ylabel("% da carteira")
    ax.legend(loc="lower right")
    ax.tick_params(axis="x", rotation=45, labelsize=7)
    ax.xaxis.set_major_locator(plt.MaxNLocator(14))
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "alocacao_rv_rf.png", dpi=140)
    plt.close(fig)

    # 3. drawdown
    fig, ax = plt.subplots(figsize=(11, 4))
    for variante, cor in [("contraria", "#1f77b4"), ("invertida", "#ff7f0e")]:
        s = resultados[variante]["serie"]
        pico, dd = 0.0, []
        for p in s:
            pico = max(pico, p["patrimonio"])
            dd.append((p["patrimonio"] / pico - 1) * 100)
        ax.plot([p["data"] for p in s], dd, label=variante, color=cor)
        ax.fill_between([p["data"] for p in s], dd, 0, color=cor, alpha=0.2)
    ax.set_title("Drawdown (queda desde o topo)")
    ax.set_ylabel("%")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.tick_params(axis="x", rotation=45, labelsize=7)
    ax.xaxis.set_major_locator(plt.MaxNLocator(14))
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "drawdown.png", dpi=140)
    plt.close(fig)
    print(f"  gráficos salvos em {config.OUTPUTS_DIR}")


PROMPT_CARTA = """Você é {NOME_DO_ROBO}, o robô gestor deste fundo. Com base no JSON abaixo
(carteira do mês, sentimento do mercado, mudanças vs. mês anterior e vetos),
escreva um comentário de gestor de NO MÁXIMO 6 linhas, em português claro,
tom profissional e direto, explicando: o humor do mercado, a alocação
entre bolsa e Tesouro Selic escolhida e 1-2 destaques da carteira (incluindo
qualquer veto).
Não invente números que não estejam no JSON.
JSON: {JSON_DO_MES}"""


def gerar_cartas(resultados: dict, limite: int | None = None) -> int:
    """Cartas mensais do KRON (prompt congelado 9.3). Idempotente: só gera as que faltam."""
    from utils import gemini_client
    existentes = {(c["mes"][:7], c["variante"]) for c in
                  db.selecionar("cartas", {"select": "mes,variante"}, order="mes")}
    # cartas são saída narrativa (não entram em nenhum cálculo) -> modelo secundário,
    # preservando a cota do modelo pinado para o sentimento (DECISOES.md item 49)
    cliente = gemini_client.GeminiClient(modelo=config.GEMINI_MODEL_SECUNDARIO)
    geradas = 0
    for variante in VARIANTES:
        anterior = None
        for carteira in resultados[variante]["carteiras"]:
            if (carteira["mes"], variante) in existentes:
                anterior = carteira
                continue
            if limite and geradas >= limite:
                return geradas
            pct_rv_ef = carteira.get("pct_rv_efetivo", carteira["pct_rv"])
            top = [p for p in carteira["posicoes"] if p["peso"] > 0][:5]
            vetadas = [p["ticker"] for p in carteira["posicoes"]
                       if p["classificacao_final"] == "vetado"]
            entrantes = ([] if anterior is None else
                         [p["ticker"] for p in top if p["ticker"] not in
                          {a["ticker"] for a in anterior["posicoes"] if a["peso"] > 0}])
            resumo = {
                "mes": carteira["mes"], "variante": variante,
                "sentimento_mercado": carteira["s_mercado"],
                "pct_bolsa": round(pct_rv_ef * 100, 1),
                "pct_tesouro_selic": round(100 - pct_rv_ef * 100, 1),
                # peso na MESMA base de pct_bolsa (fatia do patrimônio total). Antes o
                # peso ia como fatia da RV e o LLM publicava posições maiores que as
                # reais ao lado do pct_bolsa (DECISOES.md item 55).
                "principais_posicoes": [{"ticker": p["ticker"],
                                         "peso_pct_do_patrimonio": round(p["peso"] * pct_rv_ef * 100, 1),
                                         "sentimento": p["sentimento_noticias"],
                                         "n_noticias": p["n_noticias"]} for p in top],
                "entraram_no_mes": entrantes, "vetadas": vetadas,
            }
            try:
                r = cliente.gerar(PROMPT_CARTA
                                  .replace("{NOME_DO_ROBO}", NOME_ROBO)
                                  .replace("{JSON_DO_MES}", json.dumps(resumo, ensure_ascii=False)))
            except (gemini_client.CotaEsgotadaError, gemini_client.ServicoIndisponivelError) as e:
                print(f"  cartas interrompidas ({type(e).__name__}) — reexecutar continua do ponto")
                return geradas
            db.upsert("cartas", [{"mes": carteira["mes"] + "-01", "variante": variante,
                                  "texto": r["texto"].strip()}], on_conflict="mes,variante")
            geradas += 1
            anterior = carteira
    return geradas


def exportar_auditoria(resultados: dict, indicadores: dict) -> None:
    """CSV com as variáveis SEPARADAS de cada posição (exigência de auditoria do time)."""
    import csv
    destino = config.OUTPUTS_DIR / "auditoria_posicoes.csv"
    with destino.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["mes", "variante", "ticker", "peso", "sentimento_noticias", "n_noticias",
                    "flags_relatorio", "tendencia_numerica", "classificacao_final",
                    "s_mercado", "pct_rv_teorico", "pct_rv_efetivo"])
        for variante in VARIANTES:
            for c in resultados[variante]["carteiras"]:
                for p in c["posicoes"]:
                    w.writerow([c["mes"], variante, p["ticker"], p["peso"],
                                p["sentimento_noticias"], p["n_noticias"], p["flags_relatorio"],
                                p["tendencia_numerica"], p["classificacao_final"],
                                c["s_mercado"], c["pct_rv"], c.get("pct_rv_efetivo")])
    print(f"  auditoria: {destino}")


def escrever_resumo(tabela: dict, resultados: dict, dados: dict, indicadores: dict) -> None:
    """RESUMO_EXECUTIVO.md com os números que vão para o PDF do relatório."""
    linhas = ["# RESUMO EXECUTIVO — QuantAI (robô KRON)", ""]
    linhas.append(f"Período do backtest: **{config.BACKTEST_INICIO} a {config.BACKTEST_FIM}** "
                  f"(decisão mensal, execução no 1º pregão do mês seguinte).")
    n_class = db.contar("sentimentos")
    n_not = db.contar("noticias", {"titulo": "not.is.null"})
    linhas += ["", f"Corpus de mídia: **{n_not} notícias** com título; "
                   f"**{n_class} classificadas** ({100*n_class/max(1,n_not):.0f}%).", ""]

    linhas += ["## Resultados vs. benchmarks", "",
               "| estratégia | retorno total | CAGR | vol. anual | Sharpe | max. drawdown | turnover médio |",
               "|---|---|---|---|---|---|---|"]
    rotulos = {"hermes_contraria": "**KRON (contrária)**", "hermes_invertida": "**KRON (invertida)**",
               "ibovespa": "Ibovespa", "selic": "Tesouro Selic", "60_40": "60/40 (Ibov/Selic)"}
    for chave, rotulo in rotulos.items():
        m = tabela.get(chave)
        if not m:
            continue
        linhas.append(f"| {rotulo} | {m['retorno_total']*100:.1f}% | {m['cagr']*100:.1f}% | "
                      f"{m['vol_anual']*100:.1f}% | {m['sharpe']:.2f} | {m['max_drawdown']*100:.1f}% | "
                      f"{m.get('turnover_medio',0)*100:.1f}% |")

    linhas += ["", "## Retorno ano a ano", "",
               "| ano | contrária | invertida | Ibovespa | Selic |", "|---|---|---|---|---|"]
    anos = sorted(tabela["hermes_contraria"].get("por_ano", {}))
    for ano in anos:
        def r(k):
            return f"{tabela[k]['por_ano'].get(ano, 0)*100:.1f}%"
        linhas.append(f"| {ano} | {r('hermes_contraria')} | {r('hermes_invertida')} | "
                      f"{r('ibovespa')} | {r('selic')} |")

    flags = db.selecionar("flags_relatorio", {"select": "ticker,tipo_risco,mes_evento,veto_de,veto_ate,citacao_literal",
                                              "validacao_literal": "is.true"}, order="id")
    linhas += ["", f"## Vetos aplicados ({len(flags)})", ""]
    if flags:
        linhas += ["| empresa | risco | evento | veto de | veto até | citação (validada por código) |",
                   "|---|---|---|---|---|---|"]
        for f in flags:
            cit = (f["citacao_literal"] or "")[:110].replace("|", "/").replace("\n", " ")
            linhas.append(f"| {f['ticker']} | {f['tipo_risco']} | {f['mes_evento']} | "
                          f"{f['veto_de']} | {f['veto_ate']} | {cit}… |")
    else:
        linhas.append("Nenhum veto validado no período.")

    linhas += ["", "## Limitações declaradas", "",
               "- **Renda fixa simplificada:** a perna RF rende a Selic diária acumulada; "
               "desconsideramos marcação a mercado da LFT e a taxa de custódia (0,20% a.a.).",
               "- **Viés de sobrevivência:** o universo foi filtrado pela equipe com dados "
               "que incluem o período recente; empresas escolhidas por terem sido boas até hoje.",
               "- **Cobertura de mídia crescente:** de ~11/20 empresas elegíveis em 2021 para "
               "20/20 em 2026 (indexação mais densa para conteúdo recente); a carteira é mais "
               "concentrada no início do período.",
               "- **Pré-filtro de veto:** documentos da CVM sem nenhum termo de risco não foram "
               "enviados ao LLM (economia de cota); auditoria adversarial não encontrou falso negativo.",
               "- **Sem OCR:** 34 documentos escaneados não tiveram texto extraído.",
               "- **2 pregões ausentes** na fonte de preços para 8 tickers (forward-fill).",
               "", "## Próximos passos sugeridos", "",
               "- Rotular o `gabarito.csv` (150 manchetes) para medir a acurácia do classificador.",
               "- Testar sensibilidade do limiar de elegibilidade e do teto de 15% por ação.",
               "- Avaliar uso da tendência numérica (hoje só auditada) como filtro adicional.",
               ]
    destino = config.RAIZ / "RESUMO_EXECUTIVO.md"
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"  resumo executivo: {destino}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sem-cartas", action="store_true", help="pula a geração das cartas (LLM)")
    parser.add_argument("--limite-cartas", type=int, default=None, help="gera no máximo N cartas")
    args = parser.parse_args()

    dados = carregar_tudo()
    print("\nCalculando indicadores mensais (por código, sem LLM)...")
    indicadores = calcular_indicadores(dados)
    print(f"  pares escopo-mês: {len(indicadores)}")

    resultados = {}
    for variante in VARIANTES:
        print(f"\nRodando backtest — variante {variante}...")
        resultados[variante] = rodar_backtest(dados, indicadores, variante)
        print(f"  {len(resultados[variante]['serie'])} meses executados")

    teste_anti_look_ahead(dados, resultados["contraria"]["carteiras"], indicadores,
                          dados["universo"], "contraria")

    bench = benchmarks(dados)
    print("\n=== MÉTRICAS ===")
    tabela = {}
    for variante in VARIANTES:
        tabela[f"hermes_{variante}"] = metricas(resultados[variante]["serie"], dados["taxa"],
                                                resultados[variante]["turnover_medio"])
    for nome, serie in bench.items():
        tabela[nome] = metricas(serie, dados["taxa"])

    print(f"{'estratégia':<20} {'ret.total':>10} {'CAGR':>8} {'vol':>8} {'Sharpe':>8} {'maxDD':>8} {'turnover':>9}")
    for nome, m in tabela.items():
        if not m:
            continue
        print(f"{nome:<20} {m['retorno_total']*100:>9.1f}% {m['cagr']*100:>7.1f}% "
              f"{m['vol_anual']*100:>7.1f}% {m['sharpe']:>8.2f} {m['max_drawdown']*100:>7.1f}% "
              f"{m.get('turnover_medio', 0)*100:>8.1f}%")

    print("\nGravando no banco...")
    gravar_indicadores(indicadores)
    for variante in VARIANTES:
        gravar_carteiras(resultados[variante]["carteiras"])

    print("\nGerando gráficos...")
    gerar_graficos(resultados, bench)

    (config.OUTPUTS_DIR / "metricas.json").write_text(
        json.dumps(tabela, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  métricas: {config.OUTPUTS_DIR / 'metricas.json'}")

    # séries completas p/ gráficos externos (relatório final) sem reprocessar o banco
    s_mercado_serie = [{"mes": mes, "s": indicadores[("MERCADO", mes)]["s"],
                        "n": indicadores[("MERCADO", mes)]["n_noticias"]}
                       for mes in meses_do_backtest() if ("MERCADO", mes) in indicadores]
    (config.OUTPUTS_DIR / "series_backtest.json").write_text(json.dumps({
        "hermes": {v: {"serie": resultados[v]["serie"], "alocacao": resultados[v]["alocacao"],
                       "turnover_medio": resultados[v]["turnover_medio"]} for v in VARIANTES},
        "benchmarks": bench, "s_mercado": s_mercado_serie, "metricas": tabela,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"  séries: {config.OUTPUTS_DIR / 'series_backtest.json'}")

    exportar_auditoria(resultados, indicadores)

    if not args.sem_cartas:
        print("\nGerando cartas do KRON (prompt congelado 9.3)...")
        n = gerar_cartas(resultados, limite=args.limite_cartas)
        print(f"  cartas geradas nesta execução: {n} | total no banco: {db.contar('cartas')}")

    print("\nEscrevendo resumo executivo...")
    escrever_resumo(tabela, resultados, dados, indicadores)
    print("\nFASE 8 concluída.")


if __name__ == "__main__":
    main()
