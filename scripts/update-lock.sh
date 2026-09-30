#!/usr/bin/env bash
# shellcheck shell=bash
# Regenerate backend/requirements.lock (hash-pinned, linux x86_64, CPython 3.12).
# Usage: scripts/update-lock.sh [--check]   (--check fails if the lock is stale; used by CI)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../backend"
command -v uv >/dev/null || { echo "ERROR: uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }
out="requirements.lock"
[[ "${1:-}" == "--check" ]] && out="$(mktemp)"
uv pip compile pyproject.toml requirements.in --generate-hashes --quiet \
    --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
    --no-header --no-annotate -o "$out"
if [[ "${1:-}" == "--check" ]]; then
    if diff -q requirements.lock "$out" >/dev/null; then
        echo "requirements.lock is up to date"; rm -f "$out"; exit 0
    fi
    diff requirements.lock "$out" | head -20 >&2 || true
    rm -f "$out"
    echo "ERROR: backend/requirements.lock is stale — run scripts/update-lock.sh" >&2
    exit 1
fi
echo "wrote backend/$out ($(grep -c '^[a-zA-Z]' "$out") packages)"
