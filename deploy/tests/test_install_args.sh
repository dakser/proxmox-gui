#!/usr/bin/env bash
# shellcheck shell=bash
# install.sh input handling and CT ownership (F-07, F-08).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
INSTALL="$DEPLOY_DIR/install.sh"

common_shims() {
    shim_handler pvesh <<'H'
        [[ "$1 $2" == "get /cluster/nextid" ]] && echo 200
        return 0
H
    shim_handler pveam <<'H'
        [[ "$1" == available ]] && echo "system  debian-12-standard_12.7-1_amd64.tar.zst"
        return 0
H
}

test_case "HOSTNAME from the caller's environment must not become the CT hostname"
reset_env; common_shims
shim_handler pct <<'H'
    case "$1" in
        status) return 2 ;;                       # CT 200 does not exist
        exec) [[ "$*" == *"ip -4"* ]] && echo "10.0.0.5/24"; return 0 ;;
    esac
    return 0
H
HOSTNAME=pve-node-1 run_cmd bash "$INSTALL" --release v0.0.1
assert_logged "--hostname proxmox-gui" "CT hostname defaults to proxmox-gui, not the PVE node name"
assert_not_logged "--hostname pve-node-1" "PVE node hostname leaked into the CT"

test_case "a flag without a value is rejected with a clear message"
reset_env; common_shims
run_cmd bash "$INSTALL" --cpu
assert_rc_nonzero "--cpu without value"
assert_contains "$ERR" "requires a value" "error message for missing flag value"
assert_not_contains "$ERR" "unbound variable" "no raw bash error"

test_case "non-numeric CPU is rejected before touching the host"
reset_env; common_shims
run_cmd bash "$INSTALL" --cpu abc --release v0.0.1
assert_rc_nonzero "--cpu abc"
assert_not_logged "pct create" "no CT created with invalid input"

test_case "an existing CT without the proxmox-gui marker is never touched"
reset_env; common_shims
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;   # CT exists
        config) echo "hostname: someone-elses-db"; echo "tags: prod"; return 0 ;;
    esac
    return 0
H
run_cmd bash "$INSTALL" --ctid 100 --release v0.0.1
assert_rc_nonzero "foreign CTID"
assert_not_logged "pct exec" "nothing executed inside a foreign CT"
assert_not_logged "pct push" "nothing pushed into a foreign CT"

finish
