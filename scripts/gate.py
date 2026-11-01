#!/usr/bin/env python3
"""Puerta automática de una fase. Lee gates.toml, corre todo y decide. Solo el humano la cambia.

Uso: scripts/gate.py F1            -> sale con 0 solo si TODA la puerta pasa
     scripts/gate.py F1 --quick    -> sin `full` ni laboratorio (las rondas de corrección usan esta)
     scripts/gate.py --list
Códigos de salida: 0 aprobada, 1 no aprobada, 2 uso incorrecto, 4 solo falta el nivel 2 (lo ejecuta el humano).

Formato de gates.toml (ver el encabezado de ese archivo):
  [regression] fast, secrets          corren en TODAS las puertas
  [phase.X]    fast                   siempre; full: solo sin --quick; lab1: escenarios de nivel 1 (sin --quick)
               lab2                   nivel 2 (lo hace el humano): exige reports/lab2/<fase>.json con {"pass": true}
               metrics                clave = valor esperado, leído de los JSON de los escenarios (reports/lab/*.json)
                                      y de reports/lab2/<fase>.json. Sufijo _max -> <=, _min -> >=, otro -> ==.
               review                 lista de revisores, por defecto ["reviewer"]
Un escenario se corre SIEMPRE con lab/run.sh (borra el informe previo y exige pass=true), así que un informe viejo no
cuenta. Una métrica ausente es un fallo, nunca un pase. Escribe reports/gate-<fase>.json con cada paso y su resultado.
"""
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tail(text: str, n: int = 60) -> str:
    return "\n".join(text.splitlines()[-n:])


def run_command(cmd: str, timeout_s: int) -> dict:
    start = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s)
        ok, out, code = p.returncode == 0, p.stdout + "\n" + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        ok, code = False, "timeout"
        out = f"TIMEOUT tras {timeout_s} s\n" + str(e.stdout or "")[-4000:]
    return {"step": "command", "cmd": cmd, "ok": ok, "exit": code,
            "seconds": round(time.time() - start, 1), "tail": tail(out)}


def scenario_command(entry: str) -> str:
    """lab/scenarios/x.sh  ->  lab/run.sh x   (cualquier otro comando se deja igual)."""
    m = re.fullmatch(r"lab/scenarios/([A-Za-z0-9_]+)\.sh", entry)
    return f"lab/run.sh {m.group(1)}" if m else entry


def collect_results(phase: str) -> dict:
    """Pares clave/valor de los informes de laboratorio de esta corrida."""
    merged: dict = {}
    sources = sorted((ROOT / "reports" / "lab").glob("*.json")) + [ROOT / "reports" / "lab2" / f"{phase}.json"]
    for path in sources:
        if path.name == "selftest.json" or not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            for k, v in doc.items():
                merged.setdefault(k, (v, path.name))
    return merged


def check_metric(key: str, expected, results: dict) -> dict:
    op = "<=" if key.endswith("_max") else ">=" if key.endswith("_min") else "=="
    res = {"step": "metric", "key": key, "op": op, "expected": expected}
    if key not in results:
        res.update(ok=False, error="la métrica no aparece en ningún informe de esta corrida")
        return res
    actual, src = results[key]
    res["actual"], res["source"] = actual, src
    try:
        res["ok"] = {"==": actual == expected, "<=": actual <= expected, ">=": actual >= expected}[op]
    except TypeError:
        res["ok"] = False
        res["error"] = "tipos incomparables"
    return res


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    cfg = tomllib.loads((ROOT / "gates.toml").read_text())
    phases = cfg.get("phase", {})
    if "--list" in flags:
        print("\n".join(phases))
        return 0
    if len(args) != 1:
        print(__doc__)
        return 2
    phase = args[0]
    if phase not in phases:
        print(f"gate: la fase {phase} no está en gates.toml")
        return 2
    g, reg = phases[phase], cfg.get("regression", {})
    quick = "--quick" in flags or os.environ.get("GATE_QUICK") == "1"
    timeout_s = int(g.get("command_timeout_s", 2 * 3600))

    cmds = list(reg.get("fast", [])) + list(reg.get("secrets", [])) + list(g.get("fast", []))
    if not quick:
        cmds += list(g.get("full", [])) + [scenario_command(c) for c in g.get("lab1", [])]
        results_dir = ROOT / "reports" / "lab"
        if results_dir.is_dir():
            for stale in results_dir.glob("*.json"):
                if stale.name != "selftest.json":
                    stale.unlink()
    steps, failed = [], False
    for cmd in cmds:
        if failed:
            steps.append({"step": "command", "cmd": cmd, "ok": False, "skipped": True,
                          "tail": "omitido: un paso anterior falló"})
            print("OMIT ", cmd, flush=True)
            continue
        if cmd.startswith("lab/run.sh") and not Path(os.environ.get("PGUI_LAB_ENV_FILE", Path.home() / ".pgui-lab.env")).exists():
            steps.append({"step": "command", "cmd": cmd, "ok": False,
                          "tail": "falta ~/.pgui-lab.env (ver docs/hardening/BRIEF-OPENCODE.md §1)"})
            failed = True
            print("FALLA", cmd, "(sin ~/.pgui-lab.env)", flush=True)
            continue
        steps.append(run_command(cmd, timeout_s))
        failed = not steps[-1]["ok"]
        print(("OK   " if steps[-1]["ok"] else "FALLA"), cmd, flush=True)

    pending_human = False
    if not quick:
        results = collect_results(phase)
        for key, expected in g.get("metrics", {}).items():
            if failed:
                steps.append({"step": "metric", "key": key, "ok": False, "skipped": True,
                              "tail": "omitido: un paso anterior falló"})
                continue
            s = check_metric(key, expected, results)
            if not s["ok"] and "error" in s and g.get("lab2") and not (ROOT / "reports" / "lab2" / f"{phase}.json").exists():
                s["human"] = True  # la mide el nivel 2, que aún no ha corrido el humano
            steps.append(s)
            print(("OK   " if s["ok"] else "FALLA"), f"{key} {s['op']} {expected} (real: {s.get('actual', s.get('error'))})", flush=True)
        if g.get("lab2"):
            report2 = ROOT / "reports" / "lab2" / f"{phase}.json"
            try:
                ok2 = json.loads(report2.read_text()).get("pass") is True
            except (OSError, json.JSONDecodeError):
                ok2 = False
            steps.append({"step": "lab2", "file": f"reports/lab2/{phase}.json", "ok": ok2, "guide": g["lab2"],
                          "tail": "" if ok2 else "pendiente: lo ejecuta el humano (docs/hardening/LAB-RC.md)"})
            pending_human = not ok2
            print(("OK   " if ok2 else "PEND "), f"nivel 2 ({phase})", flush=True)

    only_human = pending_human and all(s["ok"] or s.get("human") for s in steps if s["step"] != "lab2")
    passed = all(s["ok"] for s in steps)
    report = {"phase": phase, "passed": passed, "pending_human": only_human, "quick": quick,
              "review": g.get("review", ["reviewer"]), "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": steps}
    out = ROOT / "reports" / f"gate-{phase}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"gate {phase}: {'APROBADA' if passed else 'SOLO FALTA EL NIVEL 2' if only_human else 'NO APROBADA'} -> {out.relative_to(ROOT)}")
    return 0 if passed else 4 if only_human else 1


if __name__ == "__main__":
    sys.exit(main())
