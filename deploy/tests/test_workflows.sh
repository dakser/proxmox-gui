#!/usr/bin/env bash
# shellcheck shell=bash
# Workflow hygiene (P0-06/P6-02): every action pinned by a 40-hex SHA, least-privilege permissions,
# credentials not persisted, and the release workflow never signs or holds write access while building.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
WF="$REPO_DIR/.github/workflows"

test_case "every 'uses:' is pinned to a full commit SHA"
while IFS= read -r line; do
    if [[ "$line" =~ uses:\ [^@]+@[0-9a-f]{40}([[:space:]]|$) ]]; then _report ok; else _report FAIL "unpinned action: $line"; fi
done < <(grep -hE '^\s*-?\s*uses:' "$WF"/*.yml)

test_case "top-level permissions are restricted; checkouts do not persist credentials"
for f in "$WF"/*.yml; do
    if grep -qE '^permissions:\s*(\{\}|$)' "$f" || grep -qE '^permissions:$' "$f"; then _report ok; else _report FAIL "$(basename "$f"): no restrictive top-level permissions"; fi
    n_checkout="$(grep -c 'actions/checkout@' "$f" || true)"
    n_nopersist="$(grep -c 'persist-credentials: false' "$f" || true)"
    assert_eq "$n_checkout" "$n_nopersist" "$(basename "$f"): every checkout sets persist-credentials: false"
done

test_case "the release workflow: read-only build job, write only in the publish job, never signs"
r="$(grep -vE "^[[:space:]]*#" "$WF/release.yml")"
assert_contains "$r" "push:" "triggered by a tag push"
assert_contains "$r" 'tags: ["v*"]' "only v* tags"
build_block="$(sed -n '/^  build:/,/^  draft-release:/p' "$WF/release.yml")"
assert_not_contains "$build_block" "contents: write" "build job has no write access"
assert_not_contains "$build_block" "GH_TOKEN" "build job has no token"
assert_contains "$(sed -n '/^  draft-release:/,$p' "$WF/release.yml")" "contents: write" "publish job has write access"
assert_contains "$r" "--draft" "release is created as a draft"
assert_not_contains "$r" "ssh-keygen -Y sign" "CI never signs (D3)"
assert_not_contains "$r" "secrets." "no secrets used"
assert_contains "$r" "pnpm/action-setup" "pnpm installed"
assert_contains "$r" "build-release.sh" "uses the shared build script"

test_case "ci.yml security scans are blocking (P6-06)"
c="$(grep -vE "^[[:space:]]*#" "$WF/ci.yml")"
assert_not_contains "$c" "continue-on-error" "no continue-on-error anywhere in ci.yml"
assert_contains "$c" "bandit" "bandit runs"
assert_contains "$c" "pip-audit" "pip-audit runs"
assert_contains "$c" "pnpm audit" "pnpm audit runs"

finish
