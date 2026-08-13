"""Supervisor da FASE 7 (classificação de sentimento).

Mesmo padrão do supervisor_veto: a classificação para de forma limpa quando a
cota estoura ou o Gemini fica instável; este processo relança sozinho enquanto
houver manchetes pendentes. Espera 1 h em caso de cota (renova por dia) e 5 min
em caso de instabilidade. Para após 4 rodadas sem progresso.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import db  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
ESPERA_INSTABILIDADE = 300
ESPERA_COTA = 3600
MAX_RODADAS_SEM_PROGRESSO = 4


def classificadas() -> int:
    return db.contar("sentimentos")


def pendentes() -> int:
    return db.contar("noticias", {"titulo": "not.is.null"}) - classificadas()


def main():
    sem_progresso = 0
    while True:
        falta = pendentes()
        if falta <= 0:
            print("[supervisor] todas as manchetes classificadas — encerrando", flush=True)
            return
        antes = classificadas()
        print(f"[supervisor] {falta} manchetes pendentes — iniciando rodada", flush=True)
        r = subprocess.run([PYTHON, str(RAIZ / "scripts" / "07_classificacao_llm.py"), "completo"],
                           cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
        saida = r.stdout or ""
        for linha in saida.splitlines():
            if any(m in linha for m in ("Resumo:", "Cota", "instável", "classificadas")):
                print(f"  {linha}", flush=True)
        progresso = classificadas() - antes
        print(f"[supervisor] rodada classificou {progresso} manchetes "
              f"({pendentes()} restantes)", flush=True)
        if pendentes() <= 0:
            print("[supervisor] concluído", flush=True)
            return
        sem_progresso = sem_progresso + 1 if progresso == 0 else 0
        if sem_progresso >= MAX_RODADAS_SEM_PROGRESSO:
            print("[supervisor] 4 rodadas sem progresso — parando para diagnóstico humano", flush=True)
            return
        espera = ESPERA_COTA if "Cota" in saida else ESPERA_INSTABILIDADE
        print(f"[supervisor] aguardando {espera // 60} min", flush=True)
        time.sleep(espera)


if __name__ == "__main__":
    main()
