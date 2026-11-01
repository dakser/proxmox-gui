#!/usr/bin/env python3
"""Comprueba que las pruebas de aceptación del manifiesto existen (tests/acceptance/manifest.toml).

Uso:  scripts/accept_check.py <fase>     p. ej. F1: exige las pruebas de F0 y F1
      scripts/accept_check.py <fase> --mutations [--task F1.2]
                                          exige que docs/BLOCKERS.md tenga una línea
                                          "MUTACIÓN <prueba>: detectada" por cada prueba py/ts/sh
                                          (de la tarea indicada, o de todas hasta la fase)
      scripts/accept_check.py --list
Sale con 1 si falta un archivo, si el nombre no aparece en el archivo o si una entrada no declara regla/prueba/mutación.
Esto impide que una prueba renombrada o borrada cuente como aprobada. Es parte del kit: los agentes no lo editan.
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PHASES = ["F0", "F1", "F2", "F3", "F4"]
KINDS = {"py", "ts", "sh", "lab", "gate"}


def load(manifest: Path) -> list[dict]:
    return tomllib.loads(manifest.read_text()).get("test", [])


def check(tests: list[dict], phase: str, root: Path) -> list[str]:
    errors: list[str] = []
    upto = PHASES.index(phase)
    seen: set[tuple[str, str]] = set()
    for t in tests:
        name = t.get("name", "?")
        for k in ("name", "phase", "kind", "files", "proves", "mutation"):
            if not t.get(k):
                errors.append(f"{name}: falta el campo '{k}'")
        if t.get("kind") not in KINDS:
            errors.append(f"{name}: tipo inválido {t.get('kind')!r}")
        if t.get("phase") not in PHASES:
            errors.append(f"{name}: fase inválida {t.get('phase')!r}")
            continue
        if (name, t["phase"]) in seen:
            errors.append(f"{name}: duplicada")
        seen.add((name, t["phase"]))
        if PHASES.index(t["phase"]) > upto:
            continue
        for f in t.get("files", []):
            p = root / f
            if not p.is_file():
                errors.append(f"{name}: no existe {f}")
            elif not re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", p.read_text(errors="replace")) \
                    and name not in Path(f).stem:
                errors.append(f"{name}: el nombre no aparece en {f}")
    return errors


def mutation_errors(tests: list[dict], phase: str, task: str | None, blockers: str) -> list[str]:
    upto = PHASES.index(phase)
    errors = []
    for t in tests:
        if t.get("kind") not in ("py", "ts", "sh") or not t.get("task") or PHASES.index(t["phase"]) > upto:
            continue
        if task and t.get("task") != task:
            continue
        if not re.search(r"^\s*[-*]?\s*MUTACI[ÓO]N\s+" + re.escape(t["name"]) + r"\s*:\s*detectada\b", blockers, re.M):
            errors.append(f"{t['name']}: falta 'MUTACIÓN {t['name']}: detectada' en docs/BLOCKERS.md "
                          f"(mutación: {t['mutation']})")
    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tests = load(ROOT / "tests" / "acceptance" / "manifest.toml")
    if sys.argv[1] == "--list":
        for t in tests:
            print(t["phase"], t["kind"], t["name"])
        return 0
    if sys.argv[1] not in PHASES:
        print(f"fase desconocida: {sys.argv[1]}")
        return 2
    errors = check(tests, sys.argv[1], ROOT)
    if "--mutations" in sys.argv:
        task = sys.argv[sys.argv.index("--task") + 1] if "--task" in sys.argv else None
        bl = ROOT / "docs" / "BLOCKERS.md"
        errors += mutation_errors(tests, sys.argv[1], task, bl.read_text() if bl.exists() else "")
    for e in errors:
        print("accept_check:", e)
    if errors:
        return 1
    n = sum(1 for t in tests if PHASES.index(t["phase"]) <= PHASES.index(sys.argv[1]))
    print(f"accept_check: {n} pruebas hasta {sys.argv[1]}, todas presentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
