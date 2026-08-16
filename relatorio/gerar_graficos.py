"""Gráficos do relatório final (tema escuro KRON, paleta validada da skill dataviz).

Lê outputs/series_backtest.json (exportado pelo 08_backtest.py) e gera PNGs
transparentes em relatorio/assets/. Séries seguem a ordem fixa de slots da
paleta de referência (modo escuro); benchmarks tracejados = codificação
secundária além da cor.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

RAIZ = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "assets"
ASSETS.mkdir(exist_ok=True)

# --- tema KRON (superfície navy escura; tinta e grade da paleta de referência)
COR = {
    "kron": "#f0b453",        # dourado da identidade (contrária = robô principal)
    "kron_inv": "#3987e5",    # slot 1 dark validado (variante invertida)
    "ibov": "#898781",        # muted ink (benchmark recessivo)
    "selic": "#199e70",       # slot 3 dark validado
    "misto": "#9085e9",       # slot 7 dark validado
    "neg": "#e66767",         # slot 8 dark validado (pessimismo)
    "ink": "#ffffff",
    "ink2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#26334d",
    "baseline": "#3a4763",
    "rf_area": "#22447a",
}
plt.rcParams.update({
    "font.family": ["Bahnschrift", "Segoe UI", "DejaVu Sans"],
    "text.color": COR["ink2"],
    "axes.edgecolor": COR["baseline"],
    "axes.labelcolor": COR["ink2"],
    "xtick.color": COR["muted"],
    "ytick.color": COR["muted"],
    "axes.facecolor": "none",
    "figure.facecolor": "none",
    "savefig.facecolor": "none",
    "axes.grid": True,
    "grid.color": COR["grid"],
    "grid.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "font.size": 8.5,
})


def _eixo_limpo(ax):
    ax.spines["bottom"].set_color(COR["baseline"])
    ax.tick_params(length=0)
    ax.grid(axis="x", visible=False)


def _datas_para_x(datas):
    """converte 'AAAA-MM-DD' em índice float de anos para eixo contínuo."""
    return [int(d[:4]) + (int(d[5:7]) - 1) / 12 + (int(d[8:10]) - 1) / 365 for d in datas]


def grafico_patrimonio(dados):
    fig, ax = plt.subplots(figsize=(11.0, 2.95), dpi=200)
    series = [
        ("KRON (contrária)", dados["hermes"]["contraria"]["serie"], COR["kron"], "-", 2.6),
        ("KRON (invertida)", dados["hermes"]["invertida"]["serie"], COR["kron_inv"], "-", 1.8),
        ("Ibovespa", dados["benchmarks"]["ibovespa"], COR["ibov"], (0, (4, 2)), 1.4),
        ("Tesouro Selic", dados["benchmarks"]["selic"], COR["selic"], (0, (4, 2)), 1.4),
        ("60/40", dados["benchmarks"]["60_40"], COR["misto"], (0, (1, 1.6)), 1.4),
    ]
    for nome, serie, cor, ls, lw in series:
        xs = _datas_para_x([p["data"] for p in serie])
        ys = [p["patrimonio"] * 100 for p in serie]
        ax.plot(xs, ys, color=cor, linestyle=ls, linewidth=lw, solid_capstyle="round")
    # rótulos diretos nos 2 protagonistas (regra de alívio p/ contraste)
    for nome, serie, cor, dy in [("KRON (contrária)", series[0][1], COR["kron"], 6),
                                 ("KRON (invertida)", series[1][1], COR["kron_inv"], -10)]:
        x = _datas_para_x([serie[-1]["data"]])[0]
        y = serie[-1]["patrimonio"] * 100
        ax.annotate(f'{nome}  {y - 100:+.0f}%', (x, y), xytext=(4, dy),
                    textcoords="offset points", fontsize=8.3, color=cor, fontweight="bold")
    ax.axhline(100, color=COR["baseline"], linewidth=0.8)
    ax.set_ylabel("Patrimônio (base 100)", fontsize=8)
    ax.legend([s[0] for s in series], loc="upper left", frameon=False, fontsize=7.6,
              labelcolor=COR["ink2"], ncols=2, columnspacing=1.2, handlelength=1.6)
    _eixo_limpo(ax)
    ax.margins(x=0.01)
    xmax = max(_datas_para_x([series[0][1][-1]["data"]]))
    ax.set_xlim(left=2021, right=xmax + 0.9)  # espaço p/ rótulos diretos
    ax.set_xticks(range(2021, 2027))
    ax.set_xticklabels([str(a) for a in range(2021, 2027)])
    fig.tight_layout(pad=0.4)
    fig.savefig(ASSETS / "grafico_patrimonio.png", transparent=True)
    plt.close(fig)


def grafico_alocacao(dados):
    aloc = dados["hermes"]["contraria"]["alocacao"]
    fig, ax = plt.subplots(figsize=(5.3, 2.5), dpi=220)
    plt.rcParams["font.size"] = 9.5
    xs = _datas_para_x([p["data"] for p in aloc])
    rv = [p["pct_rv_efetivo"] * 100 for p in aloc]
    ax.fill_between(xs, 0, rv, color=COR["kron"], alpha=0.88, linewidth=0)
    ax.fill_between(xs, rv, 100, color=COR["rf_area"], alpha=0.9, linewidth=0)
    ax.plot(xs, rv, color="#0e1626", linewidth=0.7)  # vão entre as áreas
    media_rv = sum(rv) / max(1, len(rv))
    cor_rotulo_bolsa = "#0e1626" if media_rv >= 25 else COR["kron"]
    y_rotulo_bolsa = min(max(8.0, media_rv * 0.45), 30.0)
    ax.text(xs[len(xs) // 3], y_rotulo_bolsa, "BOLSA (efetivo)", fontsize=10.5,
            fontweight="bold", color=cor_rotulo_bolsa)
    ax.text(xs[len(xs) // 3], 80, "TESOURO SELIC", fontsize=10.5, fontweight="bold", color="#cfe0f5")
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 40, 60, 80, 100])
    ax.set_ylabel("% carteira", fontsize=9)
    ax.set_xticks(range(2021, 2027))
    ax.set_xticklabels([str(a) for a in range(2021, 2027)], fontsize=8.5)
    ax.tick_params(axis="y", labelsize=8.5)
    _eixo_limpo(ax)
    ax.margins(x=0)
    fig.tight_layout(pad=0.3)
    fig.savefig(ASSETS / "grafico_alocacao.png", transparent=True)
    plt.close(fig)


def grafico_sentimento(dados):
    """Paleta sol/lua do KRON: otimismo = sol (dourado), pessimismo = lua (azul-gelo)."""
    s = dados["s_mercado"]
    fig, ax = plt.subplots(figsize=(5.5, 1.72), dpi=220)
    xs = _datas_para_x([p["mes"] + "-15" for p in s])
    valores = [p["s"] for p in s]
    # sol = acima da mediana do otimismo (menos bolsa); lua = abaixo (mais bolsa).
    # No período real o S foi positivo em todos os meses, então a dualidade é
    # RELATIVA à mediana; com S negativo os dois códigos coincidem com o sinal.
    mediana = sorted(valores)[len(valores) // 2]
    corte = mediana if min(valores) >= 0 else 0.0
    cores = [COR["kron"] if v >= corte else "#7fb3e8" for v in valores]
    ax.bar(xs, valores, width=0.055, color=cores, linewidth=0)
    ax.axhline(0, color=COR["baseline"], linewidth=0.9)
    if min(valores) >= 0:
        ax.axhline(corte, color="#8291ab", linewidth=0.7, linestyle=(0, (3, 3)))
        ax.set_ylim(min(0, min(valores)) - 0.02, max(valores) * 1.42)
        ax.text(0.01, 0.965, "sol: otimismo acima da mediana, menos bolsa",
                transform=ax.transAxes, va="top", fontsize=7.6, color=COR["kron"])
        ax.text(0.01, 0.855, "lua: abaixo da mediana, mais bolsa",
                transform=ax.transAxes, va="top", fontsize=7.6, color="#7fb3e8")
    else:
        amplitude = max(0.25, max(abs(v) for v in valores) * 1.35)
        ax.set_ylim(-amplitude, amplitude)
        ax.text(xs[0], amplitude * 0.78, "otimismo (sol): menos bolsa", fontsize=7.6, color=COR["kron"])
        ax.text(xs[0], -amplitude * 0.9, "pessimismo (lua): mais bolsa", fontsize=7.6, color="#7fb3e8")
    ax.set_xticks(range(2021, 2027))
    ax.set_xticklabels([str(a) for a in range(2021, 2027)], fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    _eixo_limpo(ax)
    ax.margins(x=0.01)
    fig.tight_layout(pad=0.25)
    fig.savefig(ASSETS / "grafico_sentimento.png", transparent=True)
    plt.close(fig)


def main():
    dados = json.loads((RAIZ / "outputs" / "series_backtest.json").read_text(encoding="utf-8"))
    grafico_patrimonio(dados)
    grafico_alocacao(dados)
    grafico_sentimento(dados)
    print(f"gráficos gerados em {ASSETS}")


if __name__ == "__main__":
    main()
