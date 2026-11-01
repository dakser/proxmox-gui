#!/usr/bin/env bash
# shellcheck shell=bash
# Corre deploy/tests/acceptance/test_*.sh (material sellado de aceptación del instalador).
# Uso: deploy/tests/acceptance/run.sh [test.sh ...]    Sale con 1 si alguna falla.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if [[ $# -gt 0 ]]; then tests=("$@"); else tests=(test_*.sh); fi
failed=0
for t in "${tests[@]}"; do
    echo "== acceptance/$t"
    bash "./$t" || failed=$((failed + 1))
done
[[ "$failed" -eq 0 ]] || { echo "deploy/tests/acceptance: $failed FAILED" >&2; exit 1; }
echo "deploy/tests/acceptance: all passed"
