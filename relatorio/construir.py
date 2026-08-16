"""Monta o relatório final AAFQ.pdf (5 páginas, 16:9) a partir do template.

Passos: injeta números reais (outputs/metricas.json) e imagens em base64 no
template.html -> Edge headless imprime em PDF -> valida (páginas <= 5, 16:9)
-> conta palavras -> exporta prévias PNG das páginas para inspeção visual.
Reexecutar depois de novo backtest atualiza tudo.
"""
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent
ASSETS = AQUI / "assets"
SAIDA_HTML = AQUI / "relatorio_final.html"
SAIDA_PDF = AQUI / "AAFQ.pdf"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
if not EDGE.exists():
    EDGE = Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


def b64(caminho: Path) -> str:
    mime = "image/png" if caminho.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(caminho.read_bytes()).decode()


def pct(x: float, casas: int = 0) -> str:
    return f"{x * 100:.{casas}f}%".replace(".", ",")


def num(x: float, casas: int = 2) -> str:
    return f"{x:.{casas}f}".replace(".", ",")


def montar_tokens() -> dict:
    m = json.loads((RAIZ / "outputs" / "metricas.json").read_text(encoding="utf-8"))
    kc, ki = m["hermes_contraria"], m["hermes_invertida"]
    ib, se, mi = m["ibovespa"], m["selic"], m["60_40"]

    # contagem de manchetes classificadas (para os textos)
    sys.path.insert(0, str(RAIZ))
    from utils import db
    n_class = db.contar("sentimentos")
    n_coletadas = db.contar("noticias")  # coletadas de fato (120 sem título ficam fora da classificação)
    n_mil = f"{n_class / 1000:.1f}".replace(".", ",") + " mil"
    n_ponto = f"{n_class:,}".replace(",", ".")
    n_coletadas_fmt = f"{n_coletadas:,}".replace(",", ".")

    dif_ibov = (kc["retorno_total"] - ib["retorno_total"]) * 100
    dif_selic = (kc["retorno_total"] - se["retorno_total"]) * 100

    if kc["sharpe"] > max(ib["sharpe"], mi["sharpe"], 0):
        titulo = f'Sharpe de {num(kc["sharpe"])}, acima dos três benchmarks'
    elif kc["retorno_total"] > ib["retorno_total"]:
        titulo = f'{abs(dif_ibov):.0f} p.p. acima do Ibovespa, com menos risco'
    else:
        titulo = "o que 66 meses de dados mostram"

    # leitura crítica data-driven em UM card compacto (frases só afirmam o que os números sustentam)
    rel = "acima do" if dif_ibov >= 0 else "abaixo do"
    rel2 = "venceu" if dif_selic > 0 else "não venceu"
    leitura = (
        f'<div class="card"><h3>Leitura crítica</h3>'
        f'<p><b class="ouro">Contra o Ibovespa:</b> {abs(dif_ibov):.0f} p.p. {rel} índice, com volatilidade de '
        f'{pct(kc["vol_anual"])} contra {pct(ib["vol_anual"])} e queda máxima de {pct(kc["max_drawdown"])} '
        f'contra {pct(ib["max_drawdown"])}.</p>'
        f'<p style="margin-top:.05in"><b class="ouro">Contra a Selic:</b> com juros médios de '
        f'{pct(se["cagr"], 1)} a.a., a estratégia {rel2} a renda fixa pura no período. As duas variantes '
        f'aparecem na tabela: a contrária é a tese e a invertida é o contrateste.</p></div>')

    estilo_p = 'class="sec" style="font-size:10.2pt; line-height:1.48"'
    if kc["sharpe"] > 0 and kc["retorno_total"] > se["retorno_total"]:
        resumo_conc = (f'Um sinal de linguagem convertido em regra fixa rendeu '
                       f'{pct(kc["retorno_total"])} em 66 meses, com Sharpe {num(kc["sharpe"])}, '
                       f'acima de bolsa, renda fixa e 60/40 em retorno por risco.')
    else:
        resumo_conc = (f'Com Selic média de {pct(se["cagr"], 1)} a.a., o sinal contrário rendeu '
                       f'{pct(kc["retorno_total"])} ({pct(kc["cagr"], 1)} a.a.) com volatilidade de '
                       f'{pct(kc["vol_anual"])}, '
                       f'{("acima" if kc["retorno_total"] > ib["retorno_total"] else "abaixo")} do Ibovespa e '
                       f'{("acima" if kc["retorno_total"] > se["retorno_total"] else "abaixo")} da renda fixa pura.')
    conclusao = (
        f'<p {estilo_p}>{resumo_conc} A operação é viável na prática: dados públicos, custo zero, '
        f'giro de {pct(kc["turnover_medio"], 1)} ao mês e decisões auditáveis linha a linha.</p>'
        f'<p {estilo_p} style="margin-top:.08in"><b style="color:#e9eef8">Pontos fortes:</b> disciplina de regra '
        f'fixa, freio CVM validado por código e replicabilidade (modelo fixado, prompts com hash).</p>'
        f'<p {estilo_p} style="margin-top:.05in"><b style="color:#e9eef8">Pontos fracos:</b> o sinal depende da '
        f'cobertura de mídia, que cresce no tempo; o classificador tem ruído medido de 12%; o universo '
        f'carrega viés de sobrevivência.</p>')

    # legenda do gráfico de sentimento reflete o que os dados REALMENTE mostraram
    series = json.loads((RAIZ / "outputs" / "series_backtest.json").read_text(encoding="utf-8"))
    s_vals = [p["s"] for p in series["s_mercado"]]
    rv_teor = [p["pct_rv"] * 100 for p in series["hermes"]["contraria"]["alocacao"]]
    if s_vals and min(s_vals) >= 0:
        n_pos = sum(1 for v in s_vals if v > 0)
        n_neutros = sum(1 for v in s_vals if v == 0)
        qualif = (f"otimista em {n_pos} dos {len(s_vals)} meses e neutro nos demais"
                  if n_neutros else f"otimista em todos os {len(s_vals)} meses")
        vmin = f"{min(rv_teor):.1f}".replace(".", ",")
        vmax = f"{max(rv_teor):.1f}".replace(".", ",")
        legenda_sent = (f"O noticiário fechou {qualif}; o sinal variou na dose. "
                        f"A regra converteu isso em alvo de bolsa entre {vmin}% e {vmax}%, "
                        f"longe dos limites de 40 e 80%.")
    else:
        legenda_sent = ("Meses de lua (pessimismo) elevam a bolsa até 80%; "
                        "meses de sol (otimismo) reduzem até 40%.")

    return {
        "LEGENDA_SENTIMENTO": legenda_sent,
        "IMG_KRON": b64(ASSETS / "kron.jpg"),
        "IMG_WORDMARK": b64(ASSETS / "kron_wordmark.png"),
        "IMG_PATRIMONIO": b64(ASSETS / "grafico_patrimonio.png"),
        "IMG_ALOCACAO": b64(ASSETS / "grafico_alocacao.png"),
        "IMG_SENTIMENTO": b64(ASSETS / "grafico_sentimento.png"),
        "N_MANCHETES_MIL": n_mil,
        "N_MANCHETES_PONTO": n_ponto,
        "N_COLETADAS": n_coletadas_fmt,
        "TITULO_RESULTADO": titulo,
        "LEITURA_CRITICA": leitura,
        "CONCLUSAO": conclusao,
        "RET_C": pct(kc["retorno_total"]), "CAGR_C": pct(kc["cagr"], 1), "VOL_C": pct(kc["vol_anual"], 1),
        "SHP_C": num(kc["sharpe"]), "DD_C": pct(kc["max_drawdown"], 1),
        "RET_I": pct(ki["retorno_total"]), "CAGR_I": pct(ki["cagr"], 1), "VOL_I": pct(ki["vol_anual"], 1),
        "SHP_I": num(ki["sharpe"]), "DD_I": pct(ki["max_drawdown"], 1),
        "RET_IBOV": pct(ib["retorno_total"]), "CAGR_IBOV": pct(ib["cagr"], 1),
        "VOL_IBOV": pct(ib["vol_anual"], 1), "SHP_IBOV": num(ib["sharpe"]), "DD_IBOV": pct(ib["max_drawdown"], 1),
        "RET_SELIC": pct(se["retorno_total"]), "CAGR_SELIC": pct(se["cagr"], 1),
        "VOL_SELIC": pct(se["vol_anual"], 1),
        "RET_6040": pct(mi["retorno_total"]), "CAGR_6040": pct(mi["cagr"], 1),
        "VOL_6040": pct(mi["vol_anual"], 1), "SHP_6040": num(mi["sharpe"]), "DD_6040": pct(mi["max_drawdown"], 1),
    }


def main():
    html = (AQUI / "template.html").read_text(encoding="utf-8")
    tokens = montar_tokens()
    for chave, valor in tokens.items():
        html = html.replace("{{" + chave + "}}", valor)
    sobras = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if sobras:
        raise SystemExit(f"tokens não preenchidos: {sorted(set(sobras))}")
    SAIDA_HTML.write_text(html, encoding="utf-8")

    subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={SAIDA_PDF}", str(SAIDA_HTML)],
                   capture_output=True, timeout=120)
    time.sleep(1)

    from pypdf import PdfReader
    leitor = PdfReader(str(SAIDA_PDF))
    n_pag = len(leitor.pages)
    box = leitor.pages[0].mediabox
    largura, altura = float(box.width) / 72, float(box.height) / 72
    texto = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"data:image[^\s\"']+", "", texto)
    palavras = len([p for p in texto.split() if any(c.isalnum() for c in p)])

    print(f"PDF: {SAIDA_PDF}")
    print(f"  páginas: {n_pag} (limite 5) {'OK' if n_pag <= 5 else '!! ESTOUROU'}")
    print(f"  formato: {largura:.3f} x {altura:.3f} pol {'OK (16:9)' if abs(largura - 13.333) < .01 and abs(altura - 7.5) < .01 else '!! ERRADO'}")
    print(f"  palavras: ~{palavras} (referência: 750) {'OK' if palavras <= 800 else '!! TEXTO DEMAIS'}")
    print(f"  tamanho: {SAIDA_PDF.stat().st_size / 1024:.0f} KB")

    # prévias PNG para inspeção visual
    import pypdfium2
    pdf = pypdfium2.PdfDocument(str(SAIDA_PDF))
    previa_dir = AQUI / "previas"
    previa_dir.mkdir(exist_ok=True)
    for i, pagina in enumerate(pdf, 1):
        img = pagina.render(scale=1.6).to_pil()
        img.save(previa_dir / f"pagina_{i}.png")
    pdf.close()
    print(f"  prévias: {previa_dir}")


if __name__ == "__main__":
    main()
