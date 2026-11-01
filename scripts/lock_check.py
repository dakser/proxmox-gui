#!/usr/bin/env python3
"""Commit-level separation of tests and code (plan §5, "Contra la autocalificación").

Usage: scripts/lock_check.py [BASE] [HEAD]      (por defecto master..HEAD)

Fails (exit 1) when a commit in the range:
  * changes code AND acceptance material (backend|frontend|deploy tests/acceptance, tests/acceptance,
    lab/scenarios, gates.toml) in the same commit; test-author commits ("T " prefix) may only touch
    acceptance material and docs/;
  * changes the specs, the plan or the kit (docs/spec, docs/plan.md, scripts/,
    orchestrator/, .claude/, .opencode/, opencode.json, AGENTS.md, CLAUDE.md, Makefile, .github/, scripts del kit), unless the subject starts with "KIT:"
    (reserved for the human who installs or updates the kit).
"""
import subprocess
import sys

ACCEPTANCE = ("tests/acceptance/", "backend/tests/acceptance/", "frontend/tests/acceptance/",
              "deploy/tests/acceptance/", "lab/scenarios/", "gates.toml")
# Kit: lo cambia solo el humano con un commit "KIT: ...". Los scripts del proyecto (update-lock.sh, build-release.sh, check.sh)
# SÍ los toca el implementador; los del kit y la firma de releases, no.
FROZEN = ("docs/spec/", "docs/plan.md", "orchestrator/", ".claude/", ".opencode/", "opencode.json",
          "AGENTS.md", "CLAUDE.md", "Makefile", ".github/", ".forgejo/", ".gitleaks.toml",
          "scripts/gate.py", "scripts/gate_selftest.py", "scripts/lock_check.py", "scripts/accept_check.py", "scripts/accept_check_selftest.py", "scripts/guard_selftest.py",
          "scripts/release-sign.sh", "deploy/release-signers",
          "lab/lib.sh", "lab/run.sh", "lab/selftest.sh", "lab/selftest/", "reports/lab2/")
NEUTRAL = ("docs/", "reports/", "README.md", "CHANGELOG.md")
TEST_AUTHOR_EXTRA = ()  # el autor de pruebas solo escribe material de aceptación y la bitácora de docs/


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def starts(path: str, prefixes) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in prefixes)


def check_commit(sha: str) -> list[str]:
    subject = git("log", "-1", "--format=%s", sha).strip()
    files = [f for f in git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).splitlines() if f]
    if subject.startswith(("KIT:", "KIT ")):
        return []  # human commits that install or repair the kit
    problems = []
    frozen = [f for f in files if starts(f, FROZEN)]
    if frozen and not subject.startswith(("KIT:", "KIT ")):
        problems.append(f"{sha[:10]} '{subject}': modifica archivos congelados {frozen}")
    acc = [f for f in files if starts(f, ACCEPTANCE)]
    code = [f for f in files if not starts(f, ACCEPTANCE) and not starts(f, FROZEN)
            and not starts(f, NEUTRAL)]
    if subject.startswith("T "):
        code = [f for f in code if not starts(f, TEST_AUTHOR_EXTRA)]
        if code:
            problems.append(f"{sha[:10]} '{subject}': commit del autor de pruebas con código {code}")
    elif acc and code:
        problems.append(f"{sha[:10]} '{subject}': mezcla pruebas de aceptación {acc} con código {code}")
    return problems


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "master"
    head = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    shas = git("rev-list", "--reverse", f"{base}..{head}").split()
    problems = [p for s in shas for p in check_commit(s)]
    for p in problems:
        print("lock-check:", p)
    if problems:
        return 1
    print(f"lock-check: {len(shas)} commits revisados, sin mezclas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
