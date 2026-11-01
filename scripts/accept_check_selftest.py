#!/usr/bin/env python3
"""Autoprueba de accept_check.py: debe detectar archivo ausente, nombre ausente, campos vacíos y duplicados."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accept_check as ac  # noqa: E402

base = {"name": "t_one", "phase": "F0", "kind": "py", "files": ["a.py"], "proves": "x", "mutation": "y"}
ok = True


def expect(desc: str, tests: list[dict], phase: str, files: dict[str, str], fails: bool) -> None:
    global ok
    with tempfile.TemporaryDirectory() as d:
        for f, body in files.items():
            Path(d, f).write_text(body)
        errs = ac.check(tests, phase, Path(d))
    good = bool(errs) == fails
    ok &= good
    print(("ok   " if good else "FALLA"), desc)


expect("todo presente pasa", [base], "F0", {"a.py": "def t_one(): pass"}, False)
expect("archivo ausente falla", [base], "F0", {}, True)
expect("nombre ausente del archivo falla", [base], "F0", {"a.py": "def other(): pass"}, True)
expect("nombre solo como subcadena de otro falla", [base], "F0", {"a.py": "def xt_one_y(): pass"}, True)
expect("sin mutación falla", [{**base, "mutation": ""}], "F0", {"a.py": "t_one"}, True)
expect("duplicada falla", [base, base], "F0", {"a.py": "t_one"}, True)
expect("fase posterior no se exige aún", [{**base, "phase": "F2"}], "F1", {}, False)
t2 = {**base, "task": "F0.1"}
good = "- MUTACIÓN t_one: detectada (quité la rama)\n"
for desc, tests, bl, task, fails in [
    ("mutación registrada pasa", [t2], good, None, False),
    ("sin línea de mutación falla", [t2], "nada", None, True),
    ("'no detectada' no cuenta", [t2], "MUTACIÓN t_one: no detectada\n", None, True),
    ("nombre parecido no cuenta", [t2], "MUTACIÓN t_one_b: detectada\n", None, True),
    ("solo se exige la tarea pedida", [t2, {**t2, "name": "t_two", "task": "F0.2"}], good, "F0.1", False),
    ("las pruebas de laboratorio no exigen mutación aquí", [{**t2, "kind": "lab"}], "", None, False),
]:
    errs = ac.mutation_errors(tests, "F0", task, bl)
    good_ = bool(errs) == fails
    ok &= good_
    print(("ok   " if good_ else "FALLA"), desc)
sys.exit(0 if ok else 1)
