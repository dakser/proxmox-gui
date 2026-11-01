#!/usr/bin/env python3
"""PreToolUse guard for proxmox-gui agents (Claude Code hook; also called by the OpenCode plugin).

Enforces, regardless of what the model decides:
  1. Secrets: nothing that reads orchestrator or lab credentials, signing keys or process environments.
  2. Per-role write permissions (PGUI_ROLE), see AGENTS.md "Roles separados".
  3. No git/gh write operations: only the orchestrator commits and opens PRs; nobody tags or releases.

Blocks by exiting with code 2; stderr is shown to the model.
The orchestrator's lock_check.py verifies the same rules after the fact,
so a command that slips past these heuristics is still caught at commit time.
"""
import json
import os
import re
import sys

# Credenciales: el archivo del orquestador, claves de proveedores, entornos de procesos, claves de firma y SSH.
# El archivo del laboratorio solo se usa a través de lab/lib.sh (lab_load_env), nunca se lee directamente.
SECRETS = re.compile(
    r"/etc/pgui-orch|orch-secrets\.env|OPENROUTER_API_KEY|OPENCODE_API_KEY|ANTHROPIC_API_KEY|GH_TOKEN|GITHUB_TOKEN"
    r"|/proc/[^/\s]+/environ|\.ssh/|id_(ed25519|rsa|ecdsa)|release-signing|\.signing-key|master\.key|jwt\.secret"
    r"|\bprintenv\b|(^|[;&|]\s*)env\s*($|[;&|])|(cat|less|more|head|tail|grep|cp|base64|xxd|od|sed|awk|strings)\s+\S*pgui-lab\.env",
    re.IGNORECASE)

# Paths each role may write (prefixes, relative to the repo root).
WRITABLE = {
    "test_author": [
        "backend/tests/acceptance/", "frontend/tests/acceptance/", "deploy/tests/acceptance/", "lab/scenarios/",
        "docs/BLOCKERS.md", "docs/TRACEABILITY.md",
    ],
    "implementer": [
        "backend/", "frontend/", "deploy/", "scripts/", "docs/", "CHANGELOG.md", "README.md",
    ],
    "reviewer": ["reports/review-"],
    "security_reviewer": ["reports/review-"],
}

# Never writable by the implementer even if a prefix above matches.
IMPLEMENTER_DENY = [
    "backend/tests/acceptance/", "frontend/tests/acceptance/", "deploy/tests/acceptance/", "tests/acceptance/",
    "lab/", "gates.toml", "docs/spec/", "docs/plan.md", "Makefile", ".github/", ".gitleaks.toml",
    "deploy/release-signers",
]

# Kit and signing: nobody writes these from an agent role. Humans do, with a "KIT: " commit.
PROTECTED_FOR_ALL = ["docs/spec/", "docs/plan.md", "orchestrator/", ".claude/", ".opencode/", "opencode.json",
                     "AGENTS.md", "CLAUDE.md", "Makefile", "gates.toml", "tests/acceptance/", ".planning/",
                     "scripts/gate.py", "scripts/lock_check.py", "scripts/accept_check.py",
                     "scripts/accept_check_selftest.py", "scripts/guard_selftest.py", "scripts/gate_selftest.py", "reports/lab2/", "orchestrator/", "scripts/release-sign.sh", ".github/", ".gitleaks.toml",
                     "lab/lib.sh", "lab/run.sh", "lab/selftest.sh", "lab/selftest/", ".git/"]

GIT_WRITE = re.compile(
    r"\bgit\s+(-C\s+\S+\s+)?(commit|push|merge|rebase|reset|checkout|switch|tag|"
    r"stash|cherry-pick|revert|am|apply|branch\s+-[dDmM]|update-ref|filter-branch|"
    r"config|add|rm|mv|restore|clean)\b"
    r"|\bgh\s+(pr\s+(merge|close|create|edit|review|comment)|release|repo|secret|workflow|issue\s+(create|edit|close))\b"
    r"|\bgh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)")

# Bash patterns that write to a path: redirections, tee, sed -i, cp/mv/rm/touch/mkdir/ln/chmod.
WRITE_OPS = re.compile(
    r"(>>?|\btee\b|\bsed\s+-i|\bperl\s+-p?i|\bcp\b|\bmv\b|\brm\b|\btouch\b|"
    r"\bmkdir\b|\bln\b|\bchmod\b|\bchown\b|\btruncate\b|\binstall\b|\brsync\b|"
    r"\bpython3?\b.*open\(|\bdd\b)")

HARMLESS_REDIR = re.compile(r"&?\d*>>?\s*/dev/null|\d*>&\d+|\d*<&\d+")


def block(msg: str) -> None:
    print(f"BLOQUEADO por guard.py: {msg}", file=sys.stderr)
    sys.exit(2)


def repo_root() -> str:
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def rel(path: str) -> str:
    root = repo_root()
    p = os.path.realpath(os.path.join(root, path)) if not os.path.isabs(path) else os.path.realpath(path)
    if p == root:
        return ""
    if not p.startswith(root + os.sep):
        return "/OUTSIDE" + p  # outside the repo
    return os.path.relpath(p, root)


def may_write(role: str, relpath: str) -> bool:
    if relpath.startswith("/OUTSIDE"):
        outside = relpath[len("/OUTSIDE"):]
        home = os.path.realpath(os.path.expanduser("~"))
        # Scratch space and tool caches outside the repo are fine; nothing else (no ~/.ssh, no /etc).
        return outside.startswith(("/tmp/", "/var/tmp/", "/run/pgui", "/var/tmp/pgui-lab/") + tuple(
            f"{home}/{d}/" for d in (".cache", ".local/share/pnpm", ".local/share/uv", ".npm", ".pnpm-store", ".local/state")))
    if any(relpath == p.rstrip("/") or relpath.startswith(p) for p in PROTECTED_FOR_ALL):
        return False
    if role == "implementer" and any(relpath == p.rstrip("/") or relpath.startswith(p)
                                     for p in IMPLEMENTER_DENY):
        return False
    allowed = WRITABLE.get(role, [])
    return any(relpath == p.rstrip("/") or relpath.startswith(p) for p in allowed)


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # not our business; never break the session on bad input
    tool = data.get("tool_name", "")
    tin = data.get("tool_input", {}) or {}
    role = os.environ.get("PGUI_ROLE", "")
    if not role:
        sys.exit(0)  # interactive human session: the guard only applies to autonomous roles

    # 1. Secrets (orchestrator credentials, provider keys, signing keys, process environments).
    for s_ in all_strings(tin):
        if SECRETS.search(s_):
            block("las credenciales y claves no son accesibles para los agentes (AGENTS.md).")

    # 2. File writes through edit tools.
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tin.get("file_path") or tin.get("notebook_path") or ""
        r = rel(path)
        if not may_write(role, r):
            block(f"el rol '{role}' no puede escribir '{r}' (AGENTS.md, roles separados).")
        sys.exit(0)

    # 3. Bash: git writes and writes to forbidden paths.
    if tool == "Bash":
        cmd = tin.get("command", "")
        if GIT_WRITE.search(cmd):
            block("solo el orquestador hace operaciones de escritura en git (AGENTS.md).")
        # Harmless redirections (2>/dev/null, 2>&1, >/dev/null) are not writes.
        cmd_w = HARMLESS_REDIR.sub(" ", cmd)
        if WRITE_OPS.search(cmd_w):
            protected = list(PROTECTED_FOR_ALL)
            if role == "implementer":
                protected += IMPLEMENTER_DENY
            for p in protected:
                if re.search(r"(^|[\s'\"=/])" + re.escape(p.rstrip("/")) + r"(/|\b)", cmd_w):
                    block(f"el comando parece escribir en '{p}', prohibido para '{role}'.")
    sys.exit(0)


if __name__ == "__main__":
    main()
