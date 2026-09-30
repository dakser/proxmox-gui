#!/usr/bin/env bash
# shellcheck shell=bash
# Single verification entry point (docs/hardening/AUTONOMY.md). Stops at the
# first failure. Run from anywhere:  scripts/check.sh
#
# Tooling expected on PATH: shellcheck, ruff, mypy, pytest (Python 3.12 env with
# backend deps), pnpm, and optionally perl/sqlite3/ssh-keygen for deploy tests.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=baseline-counts.env
source scripts/baseline-counts.env

step() { printf '\n==> %s\n' "$*"; }

step "perl -c on the SSH gate"
perl -c deploy/host/proxmox-gui-ssh-gate

step "shellcheck + bash -n on shell scripts"
mapfile -t SH_FILES < <(
    { git ls-files 'deploy/*.sh' 'scripts/*.sh' 'deploy/**/*.sh'
      git ls-files --others --exclude-standard 'deploy/*.sh' 'scripts/*.sh' 'deploy/**/*.sh'
      # extension-less shell scripts (shims, gate, updater)
      grep -rIl --exclude-dir=node_modules '^#!/usr/bin/env bash\|^#!/bin/bash' deploy scripts 2>/dev/null
    } | sort -u
)
for f in "${SH_FILES[@]}"; do
    [[ -f "$f" ]] || continue
    bash -n "$f"
done
if ((${#SH_FILES[@]})); then
    shellcheck -x "${SH_FILES[@]}"
fi

step "release-signers / pins placeholders"
# Placeholders make the installer refuse to run, so they must be filled before a
# release. PGUI_ALLOW_PLACEHOLDERS=1 (set by CI so forks can run it before they
# own a signing key) downgrades them to warnings.
placeholder_fail() {
    if [[ "${PGUI_ALLOW_PLACEHOLDERS:-0}" == 1 ]]; then echo "WARN: $1 (allowed by PGUI_ALLOW_PLACEHOLDERS=1)" >&2
    else echo "FAIL: $1" >&2; exit 1; fi
}
scripts/sync-signers.sh --check
if [[ -f deploy/release-signers ]] && grep -q 'REEMPLAZAR-CON-TU-CLAVE-PUBLICA' deploy/release-signers; then
    placeholder_fail "deploy/release-signers still holds the placeholder key (HUMAN-TODO)"
fi
if [[ -f deploy/pins.env ]] && grep -q 'TODO-PIN' deploy/pins.env; then
    placeholder_fail "deploy/pins.env has TODO-PIN entries; run scripts/update-pins.sh"
fi

step "ruff (ratchet: <= $RUFF_MAX)"
(
    cd backend
    n="$( (ruff check . --output-format=concise 2>/dev/null || true) | grep -cE '^[^ ]+:[0-9]+:[0-9]+:' || true)"
    echo "ruff errors: $n (max $RUFF_MAX)"
    [[ "$n" -le "$RUFF_MAX" ]]
)

step "mypy (ratchet: <= $MYPY_MAX)"
(
    cd backend
    n="$( (mypy --config-file mypy.ini app 2>/dev/null || true) | grep -c ': error:' || true)"
    echo "mypy errors: $n (max $MYPY_MAX)"
    [[ "$n" -le "$MYPY_MAX" ]]
)

step "backend pytest"
(cd backend && pytest -q -p no:cacheprovider)

step "frontend check + test"
(
    cd frontend
    pnpm install --frozen-lockfile >/dev/null
    pnpm check
    pnpm test
)

step "deploy script tests (harness with fake host binaries)"
deploy/tests/run.sh

printf '\ncheck.sh: OK\n'
