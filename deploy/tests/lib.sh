#!/usr/bin/env bash
# shellcheck shell=bash
# Minimal assertion library for deploy/tests (no external dependencies).
#
# Each test file sources this, calls setup_env, exercises a script through
# run_cmd, and finishes with finish. Shim behaviour is configured per test by
# writing "$SHIM_CFG/<name>.sh" defining `handle()`; every shim invocation is
# logged (shell-quoted, one line per call) to "$SHIM_LOG".

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$TESTS_DIR/.." && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
export TESTS_DIR DEPLOY_DIR REPO_DIR

_fail=0
_pass=0
_current="(no test)"

setup_env() {
    T_TMP="$(mktemp -d)"
    export T_TMP
    export SHIM_CFG="$T_TMP/cfg"
    export SHIM_LOG="$T_TMP/shim.log"
    export PGUI_ROOT="$T_TMP/root"
    mkdir -p "$SHIM_CFG" "$PGUI_ROOT"
    : >"$SHIM_LOG"
    export PATH="$TESTS_DIR/shims:$PATH"
    trap 'rm -rf "$T_TMP"' EXIT
}

# reset_env: clear shim log/config between cases inside one test file.
reset_env() {
    rm -rf "$SHIM_CFG" "$PGUI_ROOT"
    mkdir -p "$SHIM_CFG" "$PGUI_ROOT"
    : >"$SHIM_LOG"
}

# shim_handler <name> <<'EOF' ... EOF  — body of handle() for that shim.
shim_handler() {
    { echo 'handle() {'; cat; echo '}'; } >"$SHIM_CFG/$1.sh"
}

test_case() { _current="$1"; }

# run_cmd <cmd...>: runs with a 60 s timeout; sets RC, OUT (stdout), ERR (stderr).
run_cmd() {
    local so="$T_TMP/stdout" se="$T_TMP/stderr"
    set +e
    timeout 60 "$@" >"$so" 2>"$se" </dev/null
    RC=$?
    set -e
    OUT="$(cat "$so")"
    ERR="$(cat "$se")"
    export OUT ERR
}

_report() {  # _report <ok|FAIL> <message>
    if [[ "$1" == ok ]]; then
        _pass=$((_pass + 1))
    else
        _fail=$((_fail + 1))
        printf 'FAIL [%s] %s\n' "$_current" "$2" >&2
    fi
}

assert_eq() {  # assert_eq <expected> <actual> <msg>
    if [[ "$1" == "$2" ]]; then _report ok; else _report FAIL "$3: expected '$1', got '$2'"; fi
}
assert_rc() {  # assert_rc <expected-rc> [msg]
    if [[ "$RC" == "$1" ]]; then _report ok; else
        _report FAIL "${2:-exit code}: expected $1, got $RC (stderr: ${ERR:0:300})"
    fi
}
assert_rc_nonzero() {
    if [[ "$RC" != 0 ]]; then _report ok; else _report FAIL "${1:-exit code}: expected non-zero, got 0"; fi
}
assert_contains() {  # assert_contains <haystack> <needle> <msg>
    if [[ "$1" == *"$2"* ]]; then _report ok; else _report FAIL "$3: '${1:0:300}' lacks '$2'"; fi
}
assert_not_contains() {
    if [[ "$1" != *"$2"* ]]; then _report ok; else _report FAIL "$3: '${1:0:300}' contains '$2'"; fi
}
assert_file() { if [[ -e "$1" ]]; then _report ok; else _report FAIL "${2:-missing file}: $1"; fi; }
assert_no_file() { if [[ ! -e "$1" ]]; then _report ok; else _report FAIL "${2:-unexpected file}: $1"; fi; }
assert_mode() {  # assert_mode <path> <octal>
    local m
    m="$(stat -c '%a' "$1" 2>/dev/null || echo missing)"
    assert_eq "$2" "$m" "mode of $1"
}
log_has() { grep -qF -- "$1" "$SHIM_LOG"; }
assert_logged() { if log_has "$1"; then _report ok; else _report FAIL "${2:-shim log}: expected call '$1'; log: $(head -c 600 "$SHIM_LOG")"; fi; }
assert_not_logged() { if ! log_has "$1"; then _report ok; else _report FAIL "${2:-shim log}: unexpected call '$1'"; fi; }

finish() {
    printf '%s: %d passed, %d failed\n' "$(basename "$0")" "$_pass" "$_fail"
    [[ "$_fail" -eq 0 ]]
}

# make_release <tag> [--no-gate]: builds a signed fake release under $T_TMP/rel:
#   proxmox-gui-<tag>.tar.gz (flat layout), SHA256SUMS, SHA256SUMS.sig, signers file
# and a curl shim that serves those files. Exports REL_DIR, SIGNERS, SIGN_KEY.
make_release() {
    local tag="$1" stage
    REL_DIR="$T_TMP/rel"; SIGNERS="$T_TMP/allowed_signers"; SIGN_KEY="$T_TMP/sign_key"
    export REL_DIR SIGNERS SIGN_KEY
    rm -rf "$REL_DIR" "$T_TMP/stage"; mkdir -p "$REL_DIR"
    stage="$T_TMP/stage"; mkdir -p "$stage/deploy/host" "$stage/deploy/lxc" "$stage/backend" "$stage/frontend/build"
    cp "$DEPLOY_DIR/host/proxmox-gui-ssh-gate" "$stage/deploy/host/"
    printf '#!/bin/sh\nexit 0\n' >"$stage/deploy/lxc/bootstrap.sh"
    printf '#!/bin/sh\nexit 0\n' >"$stage/deploy/lxc/update.sh"
    echo "x" >"$stage/backend/pyproject.toml"; echo "x" >"$stage/frontend/build/index.js"
    tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -czf \
        "$REL_DIR/proxmox-gui-${tag}.tar.gz" -C "$stage" backend frontend deploy
    [[ -f "$SIGN_KEY" ]] || ssh-keygen -t ed25519 -N "" -q -f "$SIGN_KEY" -C test-release
    (cd "$REL_DIR" && sha256sum "proxmox-gui-${tag}.tar.gz" >SHA256SUMS \
        && ssh-keygen -Y sign -q -f "$SIGN_KEY" -n proxmox-gui-release SHA256SUMS)
    printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "$(cut -d' ' -f1,2 "$SIGN_KEY.pub")" >"$SIGNERS"
    shim_handler curl <<'H'
        local out="" url="" prev=""
        for a in "$@"; do
            [[ "$prev" == "-o" ]] && out="$a"
            [[ "$a" == http* ]] && url="$a"
            prev="$a"
        done
        local src="$REL_DIR/$(basename "$url")"
        [[ -f "$src" ]] || return 22
        cp "$src" "$out"
H
}
