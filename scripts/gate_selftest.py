#!/usr/bin/env python3
"""Autoprueba de gate.py en un repositorio de juguete: cada decisión de la puerta se comprueba con un caso."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent / "gate.py"
bad = 0


def run(gates: str, phase: str = "F1", args=(), prep=None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "scripts").mkdir()
        shutil.copy(SRC, root / "scripts" / "gate.py")
        (root / "gates.toml").write_text(gates)
        (root / "lab").mkdir()
        (root / "lab" / "run.sh").write_text(
            '#!/usr/bin/env bash\nmkdir -p reports/lab\n[ -f "lab/$1.out" ] && cp "lab/$1.out" "reports/lab/$1.json"; exit "${LABRC:-0}"\n')
        (root / "lab" / "run.sh").chmod(0o755)
        (root / "home").mkdir()
        (root / "home" / "env").write_text("x=1\n")
        if prep:
            prep(root)
        p = subprocess.run([sys.executable, "scripts/gate.py", phase, *args], cwd=root, capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "PGUI_LAB_ENV_FILE": str(root / "home" / "env")})
        rep = root / "reports" / f"gate-{phase}.json"
        return p.returncode, json.loads(rep.read_text()) if rep.exists() else {}


def expect(desc: str, got, want) -> None:
    global bad
    ok = got == want
    bad += not ok
    print(("ok   " if ok else f"FALLA (esperado {want!r}, fue {got!r})"), desc)


BASE = '[regression]\nfast = ["true"]\nsecrets = ["true"]\n[phase.F1]\n'
rc, rep = run(BASE + 'fast = ["true"]\n')
expect("todo en verde aprueba", (rc, rep["passed"]), (0, True))
rc, rep = run(BASE + 'fast = ["false", "true"]\n')
expect("un fallo suspende y omite el resto", (rc, [s.get("skipped", False) for s in rep["steps"]][-1]), (1, True))
rc, rep = run('[regression]\nfast = ["false"]\n[phase.F1]\nfast = ["true"]\n')
expect("la regresión falla la puerta", rc, 1)
rc, rep = run(BASE + 'fast = []\nfull = ["false"]\n', args=["--quick"])
expect("--quick no corre full", rc, 0)
rc, rep = run(BASE + 'fast = []\nfull = ["false"]\n')
expect("sin --quick sí corre full", rc, 1)
m = BASE + 'fast = []\nlab1 = ["lab/scenarios/s1.sh"]\nmetrics = { ok_flag = true, score_max = 1.5, n_min = 2 }\n'


def with_report(doc):
    return lambda root: (root / "lab" / "s1.out").write_text(json.dumps(doc))


rc, _ = run(m, prep=with_report({"pass": True, "ok_flag": True, "score_max": 1.5, "n_min": 2}))
expect("métricas dentro de umbral aprueban", rc, 0)
rc, _ = run(m, prep=with_report({"pass": True, "ok_flag": False, "score_max": 1.0, "n_min": 2}))
expect("igualdad distinta falla", rc, 1)
rc, _ = run(m, prep=with_report({"pass": True, "ok_flag": True, "score_max": 1.6, "n_min": 2}))
expect("_max superado falla", rc, 1)
rc, _ = run(m, prep=with_report({"pass": True, "ok_flag": True, "score_max": 1.0, "n_min": 1}))
expect("_min no alcanzado falla", rc, 1)
rc, _ = run(m, prep=with_report({"pass": True, "ok_flag": True, "score_max": 1.0}))
expect("métrica ausente falla", rc, 1)
rc, _ = run(m, prep=lambda r: (r / "reports" / "lab").mkdir(parents=True) or (r / "reports" / "lab" / "old.json").write_text(
    json.dumps({"ok_flag": True, "score_max": 1.0, "n_min": 9})))
expect("un informe viejo no cuenta (se borra antes de medir)", rc, 1)
rc, _ = run(m + "", prep=lambda r: (r / "lab" / "s1.out").write_text('{"pass": true, "ok_flag": true, "score_max": 1, "n_min": 3}'))
expect("el escenario pasa por lab/run.sh", rc, 0)
l2 = BASE + 'fast = []\nlab2 = ["docs/hardening/LAB-RC.md"]\n'
rc, rep = run(l2)
expect("sin informe de nivel 2: solo falta el humano (4)", (rc, rep["pending_human"]), (4, True))
rc, rep = run(l2, prep=lambda r: (r / "reports" / "lab2").mkdir(parents=True) or (r / "reports" / "lab2" / "F1.json").write_text('{"pass": true}'))
expect("con nivel 2 aprobado, pasa", rc, 0)
rc, rep = run(l2.replace('fast = []', 'fast = ["false"]'))
expect("si falla otra cosa no se dice 'solo falta el humano'", (rc, rep["pending_human"]), (1, False))
rc, rep = run(BASE + 'fast = []\nlab2 = ["x"]\nmetrics = { from_lab2_max = 1.5 }\n')
expect("una métrica del nivel 2 aún sin medir cuenta como pendiente del humano", (rc, rep["pending_human"]), (4, True))
sys.exit(1 if bad else 0)
