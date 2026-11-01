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
sys.exit(0 if ok else 1)
