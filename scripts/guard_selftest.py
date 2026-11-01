#!/usr/bin/env python3
"""Autoprueba de .claude/hooks/guard.py: cada guarda debe bloquear de verdad lo prohibido y dejar pasar lo permitido."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / ".claude" / "hooks" / "guard.py"

CASES = [  # (rol, herramienta, entrada, bloquea?)
    ("implementer", "Write", {"file_path": "backend/app/foo.py"}, False),
    ("implementer", "Write", {"file_path": "scripts/update-lock.sh"}, False),
    ("implementer", "Write", {"file_path": "backend/tests/acceptance/test_x.py"}, True),
    ("implementer", "Edit", {"file_path": "frontend/tests/acceptance/a.test.ts"}, True),
    ("implementer", "Write", {"file_path": "deploy/tests/acceptance/t.sh"}, True),
    ("implementer", "Write", {"file_path": "lab/scenarios/f1.sh"}, True),
    ("implementer", "Write", {"file_path": "gates.toml"}, True),
    ("implementer", "Write", {"file_path": "docs/plan.md"}, True),
    ("implementer", "Write", {"file_path": "docs/spec/v0.1.1.md"}, True),
    ("implementer", "Write", {"file_path": "docs/hardening/DECISIONS.md"}, False),
    ("implementer", "Write", {"file_path": "AGENTS.md"}, True),
    ("implementer", "Write", {"file_path": ".claude/hooks/guard.py"}, True),
    ("implementer", "Write", {"file_path": "scripts/gate.py"}, True),
    ("implementer", "Write", {"file_path": "scripts/release-sign.sh"}, True),
    ("implementer", "Write", {"file_path": "scripts/guard_selftest.py"}, True),
    ("implementer", "Write", {"file_path": "scripts/gate_selftest.py"}, True),
    ("implementer", "Write", {"file_path": "reports/lab2/F3.json"}, True),
    ("test_author", "Write", {"file_path": "reports/lab2/F3.json"}, True),
    ("implementer", "Write", {"file_path": ".github/workflows/ci.yml"}, True),
    ("implementer", "Write", {"file_path": "deploy/release-signers"}, True),
    ("implementer", "Write", {"file_path": "../../../../../../../../var/lib/outside.txt"}, True),
    ("implementer", "Write", {"file_path": "/etc/passwd"}, True),
    ("implementer", "Write", {"file_path": "/tmp/scratch.txt"}, False),
    ("test_author", "Write", {"file_path": "backend/tests/acceptance/test_x.py"}, False),
    ("test_author", "Write", {"file_path": "lab/scenarios/f1.sh"}, False),
    ("test_author", "Write", {"file_path": "lab/run.sh"}, True),
    ("test_author", "Write", {"file_path": "backend/app/foo.py"}, True),
    ("test_author", "Write", {"file_path": "gates.toml"}, True),
    ("test_author", "Write", {"file_path": "tests/acceptance/manifest.toml"}, True),
    ("reviewer", "Write", {"file_path": "backend/app/foo.py"}, True),
    ("reviewer", "Write", {"file_path": "reports/review-F1.md"}, False),
    ("security_reviewer", "Write", {"file_path": "reports/review-sec-F1.md"}, False),
    ("implementer", "Bash", {"command": "git commit -m x"}, True),
    ("implementer", "Bash", {"command": "git -C . push origin HEAD"}, True),
    ("implementer", "Bash", {"command": "git tag v0.1.1"}, True),
    ("implementer", "Bash", {"command": "git status && git diff"}, False),
    ("implementer", "Bash", {"command": "gh pr merge 5"}, True),
    ("implementer", "Bash", {"command": "gh api -X DELETE repos/dakser/proxmox-gui"}, True),
    ("implementer", "Bash", {"command": "gh run view 123"}, False),
    ("implementer", "Bash", {"command": "echo x > gates.toml"}, True),
    ("implementer", "Bash", {"command": "sed -i s/a/b/ backend/tests/acceptance/test_x.py"}, True),
    ("implementer", "Bash", {"command": "rm -rf lab/scenarios"}, True),
    ("implementer", "Bash", {"command": "echo ok > backend/app/x.txt 2>/dev/null"}, False),
    ("implementer", "Bash", {"command": "cd backend && pytest -q tests/acceptance 2>&1 | tail"}, False),
    ("implementer", "Bash", {"command": "cat ~/.pgui-lab.env"}, True),
    ("implementer", "Bash", {"command": "printenv"}, True),
    ("implementer", "Bash", {"command": "cat /proc/self/environ"}, True),
    ("implementer", "Bash", {"command": "cat ~/.ssh/id_ed25519"}, True),
    ("implementer", "Read", {"file_path": "/etc/pgui-orch/secrets.env"}, True),
    ("implementer", "Bash", {"command": "lab/run.sh f1_new_user_can_provision"}, False),
    ("", "Write", {"file_path": "gates.toml"}, False),  # sesión humana interactiva: no aplica
]

bad = 0
for role, tool, tin, expect_block in CASES:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT), "PGUI_ROLE": role}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_name": tool, "tool_input": tin}),
                       capture_output=True, text=True, env=env, cwd=ROOT)
    blocked = r.returncode == 2
    ok = blocked == expect_block
    bad += not ok
    print(("ok   " if ok else "FALLA"), f"[{role or 'humano'}] {tool} {list(tin.values())[0]} -> {'bloquea' if blocked else 'pasa'}")
sys.exit(1 if bad else 0)
