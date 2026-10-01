#!/usr/bin/env bash
# shellcheck shell=bash
# Runs every deploy/tests/test_*.sh with fake host binaries first on PATH.
# Usage: deploy/tests/run.sh [test_name.sh ...]
#
# deploy/tests/EXPECTED-RED lists test files that are known-red on purpose
# (documented reproductions of bugs not fixed yet). They MUST fail; if one
# passes, the run fails so the entry gets removed. Empty/absent = none.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if [[ $# -gt 0 ]]; then tests=("$@"); else tests=(test_*.sh); fi
expected_red=""
[[ -f EXPECTED-RED ]] && expected_red="$(grep -v '^#' EXPECTED-RED || true)"
failed=0
for t in "${tests[@]}"; do
    echo "== $t"
    if bash "./$t"; then ok=1; else ok=0; fi
    if grep -qxF "$t" <<<"$expected_red"; then
        if [[ "$ok" -eq 1 ]]; then
            echo "UNEXPECTED PASS: $t is listed in EXPECTED-RED; remove it" >&2
            failed=$((failed + 1))
        else
            echo "(expected red: $t)"
        fi
    elif [[ "$ok" -eq 0 ]]; then
        failed=$((failed + 1))
    fi
done
if [[ "$failed" -gt 0 ]]; then
    echo "deploy/tests: $failed test file(s) FAILED" >&2
    exit 1
fi
echo "deploy/tests: all as expected"
