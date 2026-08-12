"""Orquestrador: roda o pipeline completo na ordem das fases.

Cada script é idempotente; rodar de novo não duplica dados. Scripts que
gastam créditos (03, 05, 07) exigem confirmação explícita via flag --sim.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS = [
    "00_health_check.py",
    "01_universo.py",
    "02_gdelt_noticias.py",
    "03_titulos_reais.py",     # coleta local (custo zero)
    "04_cvm_ipe.py",
    "05_google_news.py",       # gasta créditos Apify (Pay Per Event)
    "06_precos_selic.py",
    "07_classificacao_llm.py", # gasta cota Gemini
    "08_backtest.py",
]

if __name__ == "__main__":
    pasta = Path(__file__).resolve().parent
    for script in SCRIPTS:
        print(f"\n>>> Rodando {script} ...")
        r = subprocess.run([sys.executable, str(pasta / script)] + sys.argv[1:])
        if r.returncode != 0:
            print(f"!!! {script} terminou com erro ({r.returncode}). Pipeline interrompido.")
            sys.exit(r.returncode)
    print("\nPipeline completo executado com sucesso.")
