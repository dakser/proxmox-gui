#!/usr/bin/env bash
# shellcheck shell=bash
# install.sh input handling and CT ownership (F-07, F-08).
# SC2016 is disabled on purpose: hostile payloads must stay literal (unexpanded).
# shellcheck disable=SC2016
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
INSTALL="$DEPLOY_DIR/install.sh"

fresh() {  # fresh: clean state + signed release v0.0.1 + benign pvesh/pveam/pct shims
    reset_env
    make_release v0.0.1
    shim_handler pvesh <<'H'
        [[ "$1 $2" == "get /cluster/nextid" ]] && echo 200
        return 0
H
    shim_handler pveam <<'H'
        [[ "$1" == available ]] && echo "system  debian-12-standard_12.7-1_amd64.tar.zst"
        return 0
H
    shim_handler pct <<'H'
        case "$1" in
            status) return 2 ;;                       # CT does not exist yet
            exec) [[ "$*" == *"ip -4"* ]] && echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"; return 0 ;;
        esac
        return 0
H
}
inst() { run_cmd bash "$INSTALL" --signers "$SIGNERS" "$@"; }

test_case "HOSTNAME from the caller's environment must not become the CT hostname"
fresh
HOSTNAME=pve-node-1 inst --release v0.0.1
assert_rc 0 "install succeeds"
assert_logged "--hostname proxmox-gui" "CT hostname defaults to proxmox-gui, not the PVE node name"
assert_not_logged "--hostname pve-node-1" "PVE node hostname leaked into the CT"

test_case "CTID and other generic variables from the environment are ignored"
fresh
CTID=999 CPU=64 STORAGE=evil inst --release v0.0.1
assert_logged "pct create 200" "generic CTID env var is not used"
assert_logged "--cores 2" "generic CPU env var ignored"
assert_not_logged "evil" "generic STORAGE env var ignored"

test_case "PGUI_-prefixed environment is honoured, flags win"
fresh
PGUI_CPU=4 PGUI_CTID=321 inst --release v0.0.1 --cpu 3
assert_logged "pct create 321" "PGUI_CTID used"
assert_logged "--cores 3" "flag beats env"

test_case "a flag without a value is rejected with a clear message"
fresh
inst --cpu
assert_rc_nonzero "--cpu without value"
assert_contains "$ERR" "requires a value" "error message for missing flag value"
assert_not_contains "$ERR" "unbound variable" "no raw bash error"
fresh
inst --release --cpu 2
assert_rc_nonzero "--release swallowing the next flag"
assert_contains "$ERR" "requires a value" "flag-looking value rejected"

test_case "invalid values are rejected before touching the host"
for args in "--cpu abc" "--cpu 0" "--cpu 9999" "--ram 10" "--ram x" "--disk -1" "--disk 1e3" "--ctid 12" "--ctid abc" \
            "--ctid 100;id" '--storage a$(id)' "--storage ../x" "--bridge a/b" "--hostname a_b!" "--hostname -x" \
            "--repo-url http://github.com/o/r" "--repo-url https://evil.example/o/r" "--repo-url https://github.com/o/r.git" \
            "--repo-url https://github.com/o/r/extra" "--ip 1.2.3.4" "--ip 999" "--gw 1.2.3.4" "--host-ip nope"; do
    fresh
    # shellcheck disable=SC2086
    inst --release v0.0.1 $args
    assert_rc_nonzero "args: $args"
    assert_not_logged "pct create" "no CT created for: $args"
    assert_not_logged "curl" "no download for: $args"
done

test_case "--release is mandatory and must be a vX.Y.Z tag"
for rel in "" master main latest v1 "v1.2" "1.2.3" "v1.2.3;id" 'v1.2.3$(id)' "v1..2.3" "v1.2.3 x" "../v1.2.3" "v1.2.3/../x"; do
    fresh
    if [[ -z "$rel" ]]; then inst; else inst --release "$rel"; fi
    assert_rc_nonzero "release '$rel'"
    assert_not_logged "pct create" "no CT for release '$rel'"
done

test_case "an existing CT without the proxmox-gui marker is never touched"
fresh
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;   # CT exists
        config) echo "hostname: someone-elses-db"; echo "tags: prod"; return 0 ;;
    esac
    return 0
H
inst --ctid 100 --release v0.0.1
assert_rc_nonzero "install onto an existing CTID"
assert_not_logged "pct exec" "nothing executed inside a foreign CT"
assert_not_logged "pct push" "nothing pushed into a foreign CT"
inst --update --ctid 100 --release v0.0.1
assert_rc_nonzero "--update on a foreign CTID"
assert_contains "$ERR" "not a proxmox-gui container" "explains the refusal"
assert_not_logged "pct exec" "nothing executed inside a foreign CT (update)"
assert_not_logged "pct push" "nothing pushed into a foreign CT (update)"
assert_not_logged "curl" "nothing downloaded for a foreign CT"

test_case "tagged CT without the .installed marker file is refused for update"
fresh
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;
        config) echo "tags: proxmox-gui"; echo "description: proxmox-gui-managed (created by install.sh v0.0.1)"; return 0 ;;
        exec) [[ "$*" == *"test -f /etc/proxmox-gui/.installed"* ]] && return 1; return 0 ;;
    esac
    return 0
H
inst --update --ctid 150 --release v0.0.1
assert_rc_nonzero "missing .installed marker"
assert_not_logged "pct push" "nothing pushed"
assert_not_logged "bootstrap" "nothing run"
assert_not_logged "proxmox-gui-updater" "nothing run"

test_case "the created CT is unprivileged, tagged and marked"
fresh
inst --release v0.0.1
assert_logged "--unprivileged 1" "unprivileged"
assert_logged "--tags proxmox-gui" "tag"
assert_logged "proxmox-gui-managed" "description marker"
assert_not_logged "--privileged" "never privileged"
assert_not_logged "authorized_keys" "no host trust without the flag"

finish
