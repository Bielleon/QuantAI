"""Supervisor da etapa de veto (FASE 4).

O extrator para de forma LIMPA quando o Gemini fica instável (5xx) ou quando a
cota diária estoura — comportamento correto, mas que exige relançar depois.
Este supervisor faz isso sozinho: enquanto houver documentos 'extraido',
relança a etapa e espera antes de tentar de novo (5 min para instabilidade,
1 h para cota). Encerra quando a fila zera ou após 3 rodadas sem progresso.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import db  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
ESPERA_INSTABILIDADE = 300   # 5 min
ESPERA_COTA = 3600           # 1 h
MAX_RODADAS_SEM_PROGRESSO = 3


def pendentes() -> int:
    return db.contar("documentos_cvm", {"status_processamento": "eq.extraido"})


def classificados() -> int:
    """Métrica de progresso REAL: só cresce. Medir pela fila de 'extraido' engana
    quando a etapa de PDFs roda em paralelo alimentando a mesma fila."""
    return db.contar("documentos_cvm", {"status_processamento": "eq.classificado"})


def main():
    sem_progresso = 0
    while True:
        restantes = pendentes()
        if restantes == 0:
            print("[supervisor] fila de classificação vazia — encerrando", flush=True)
            return
        print(f"[supervisor] {restantes} documentos a classificar — iniciando rodada", flush=True)
        feitos_antes = classificados()
        r = subprocess.run([PYTHON, str(RAIZ / "scripts" / "04_cvm_ipe.py"), "veto"],
                           cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace")
        saida = (r.stdout or "") + (r.returncode and (r.stderr or "") or "")
        for linha in (r.stdout or "").splitlines():
            if any(m in linha for m in ("VETO", "REJEITADA", "Resumo:", "Cota", "indisponível")):
                print(f"  {linha}", flush=True)

        depois = pendentes()
        progresso = classificados() - feitos_antes
        print(f"[supervisor] rodada classificou {progresso} documentos "
              f"({depois} ainda na fila)", flush=True)
        if depois == 0:
            print("[supervisor] concluído", flush=True)
            return
        sem_progresso = sem_progresso + 1 if progresso == 0 else 0
        if sem_progresso >= MAX_RODADAS_SEM_PROGRESSO:
            print("[supervisor] 3 rodadas sem progresso — parando para diagnóstico humano", flush=True)
            return
        espera = ESPERA_COTA if "Cota" in saida else ESPERA_INSTABILIDADE
        print(f"[supervisor] aguardando {espera // 60} min antes da próxima rodada", flush=True)
        time.sleep(espera)


if __name__ == "__main__":
    main()
