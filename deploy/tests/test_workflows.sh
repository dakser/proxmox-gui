#!/usr/bin/env bash
# shellcheck shell=bash
# Workflow hygiene (P0-06/P6-02, L-18/X28): CI and release live in .forgejo/workflows, use NO third-party actions and no GitHub
# URLs; the release workflow builds without any token and only the last step gets the forge token; it never signs.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
WF="$REPO_DIR/.forgejo/workflows"

test_case "the old GitHub workflow directory is gone"
if [[ -e "$REPO_DIR/.github" ]]; then _report FAIL ".github/ still exists"; else _report ok; fi

test_case "no workflow uses an external action (uses:) nor mentions github.com"
if grep -hE '^\s*-?\s*uses:' "$WF"/*.yml >/dev/null 2>&1; then _report FAIL "a workflow uses 'uses:'"; else _report ok; fi
if grep -qiE 'github\.com|githubusercontent|api\.github' "$WF"/*.yml; then _report FAIL "workflow mentions GitHub"; else _report ok; fi

test_case "every job runs on the self-hosted runner label and checks out without persisting credentials"
for f in "$WF"/*.yml; do
    n_jobs="$(grep -cE '^    runs-on:' "$f" || true)"
    n_pgui="$(grep -cE '^    runs-on: pgui$' "$f" || true)"
    assert_eq "$n_jobs" "$n_pgui" "$(basename "$f"): all jobs run on label pgui"
    n_co="$(grep -c 'Checkout (sin acciones externas)' "$f" || true)"
    assert_eq "$n_co" "$n_jobs" "$(basename "$f"): every job checks out explicitly"
    # shellcheck disable=SC2016  # comillas simples a propósito: texto literal, no expansión
    assert_contains "$(cat "$f")" 'git remote set-url origin "$SERVER/$REPO.git"' "$(basename "$f"): checkout leaves no token in the remote"
done

test_case "the release workflow: tag-triggered, token only in the last step, never signs"
r="$(grep -vE "^[[:space:]]*#" "$WF/release.yml")"
assert_contains "$r" 'tags: ["v*"]' "only v* tags"
before_draft="$(sed -n '/Create the DRAFT release/q;p' "$WF/release.yml")"
after_checkout="$(printf '%s\n' "$before_draft" | sed -n '/Validate tag and signer configuration/,$p')"
assert_not_contains "$after_checkout" "FORGE_TOKEN" "no token while building"
assert_contains "$(sed -n '/Create the DRAFT release/,$p' "$WF/release.yml")" "FORGE_TOKEN" "the draft step has the forge token"
assert_contains "$r" '"draft": True' "release is created as a draft"
assert_not_contains "$r" "ssh-keygen -Y sign" "CI never signs (D3)"
assert_not_contains "$r" "secrets." "no secrets used"
assert_contains "$r" "build-release.sh" "uses the shared build script"

test_case "ci.yml security scans are blocking (P6-06) and CI runs on integration branches"
c="$(grep -vE "^[[:space:]]*#" "$WF/ci.yml")"
assert_not_contains "$c" "continue-on-error" "no continue-on-error anywhere in ci.yml"
assert_contains "$c" "bandit" "bandit runs"
assert_contains "$c" "pip-audit" "pip-audit runs"
assert_contains "$c" "pnpm audit" "pnpm audit runs"
assert_contains "$c" 'gitleaks detect' "gitleaks runs"
assert_contains "$c" '"integracion/**"' "push to integration branches triggers CI"

finish
