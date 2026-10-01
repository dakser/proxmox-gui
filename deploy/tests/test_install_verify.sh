#!/usr/bin/env bash
# shellcheck shell=bash
# Release authenticity (F-04): signature + hash checked on the host BEFORE the
# first pct create/push; nothing unsigned is ever pushed into the CT.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
INSTALL="$DEPLOY_DIR/install.sh"

fresh() {
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
            status) return 2 ;;
            exec) [[ "$*" == *"ip -4"* ]] && echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"; return 0 ;;
        esac
        return 0
H
}
inst() { run_cmd bash "$INSTALL" "$@"; }
expect_abort() {  # expect_abort <why>
    assert_rc_nonzero "$1"
    assert_not_logged "pct create" "$1: no CT created"
    assert_not_logged "pct push" "$1: nothing pushed"
    assert_not_logged "pct exec" "$1: nothing executed"
}

test_case "valid signed release installs; hash + signature verified first"
fresh
inst --signers "$SIGNERS" --release v0.0.1
assert_rc 0 "install"
assert_contains "$OUT" "signature and hash OK" "verification ran"
assert_logged "pct push 200" "tarball pushed after verification"
assert_logged "pct exec 200 -- env PGUI_RELEASE_TAG=v0.0.1 PGUI_SRC_DIR=/root/pgui-src PGUI_REPO_URL=https://github.com/dakser/proxmox-gui PGUI_SIGNERS_FILE=/root/pgui-allowed-signers bash /root/pgui-src/deploy/lxc/bootstrap.sh" "bootstrap runs from the verified tarball"
first_create="$(grep -n '^pct create' "$SHIM_LOG" | head -1 | cut -d: -f1)"
first_curl="$(grep -n '^curl' "$SHIM_LOG" | head -1 | cut -d: -f1)"
assert_eq "1" "$([[ "$first_curl" -lt "$first_create" ]] && echo 1 || echo 0)" "download+verify happen before pct create"
assert_not_contains "$(cat "$SHIM_LOG")" "raw.githubusercontent" "no curl|bash"
assert_not_contains "$(cat "$SHIM_LOG")" "git clone" "no git clone"

test_case "invalid signature aborts"
fresh
printf 'garbage' >"$REL_DIR/SHA256SUMS.sig"
inst --signers "$SIGNERS" --release v0.0.1
expect_abort "corrupt signature"; assert_contains "$ERR" "signature verification FAILED" "message"

test_case "signature by another key aborts"
fresh
ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/other" -C other
(cd "$REL_DIR" && rm SHA256SUMS.sig && ssh-keygen -Y sign -q -f "$T_TMP/other" -n proxmox-gui-release SHA256SUMS)
inst --signers "$SIGNERS" --release v0.0.1
expect_abort "wrong signer"

test_case "signature for another namespace aborts"
fresh
(cd "$REL_DIR" && rm SHA256SUMS.sig && ssh-keygen -Y sign -q -f "$SIGN_KEY" -n something-else SHA256SUMS)
inst --signers "$SIGNERS" --release v0.0.1
expect_abort "wrong namespace"

test_case "tampered tarball (hash mismatch) aborts"
fresh
printf 'evil' >>"$REL_DIR/proxmox-gui-v0.0.1.tar.gz"
inst --signers "$SIGNERS" --release v0.0.1
expect_abort "altered tarball"; assert_contains "$ERR" "SHA-256 mismatch" "message"

test_case "tampered SHA256SUMS (signature no longer matches) aborts"
fresh
echo "0000000000000000000000000000000000000000000000000000000000000000  extra-file" >>"$REL_DIR/SHA256SUMS"
inst --signers "$SIGNERS" --release v0.0.1
expect_abort "altered SHA256SUMS"

test_case "nonexistent tag aborts"
fresh
inst --signers "$SIGNERS" --release v9.9.9
expect_abort "missing release"

test_case "placeholder signer refuses to install"
fresh
# Use a copy of install.sh with the placeholder embedded: the real one carries the owner's key by now.
rm -rf "$T_TMP/ph"; mkdir -p "$T_TMP/ph/scripts"
cp -r "$DEPLOY_DIR" "$T_TMP/ph/deploy"; cp "$DEPLOY_DIR/../scripts/sync-signers.sh" "$T_TMP/ph/scripts/"
printf 'proxmox-gui-release namespaces="proxmox-gui-release" REEMPLAZAR-CON-TU-CLAVE-PUBLICA\n' >"$T_TMP/ph/deploy/release-signers"
"$T_TMP/ph/scripts/sync-signers.sh" >/dev/null
INSTALL="$T_TMP/ph/deploy/install.sh" inst --release v0.0.1
expect_abort "embedded placeholder key"
assert_contains "$ERR" "no release signer key configured" "message"

test_case "update path verifies too and only then touches the CT"
fresh
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;
        config) echo "tags: proxmox-gui"; echo "description: proxmox-gui-managed"; return 0 ;;
        exec) [[ "$*" == *"ip -4"* ]] && echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"; return 0 ;;
    esac
    return 0
H
printf 'garbage' >"$REL_DIR/SHA256SUMS.sig"
inst --signers "$SIGNERS" --update --ctid 150 --release v0.0.1
assert_rc_nonzero "update with bad signature"
assert_not_logged "pct push" "nothing pushed on bad signature"
assert_not_logged "proxmox-gui-updater" "updater not called on bad signature"

test_case "update path delegates to the in-LXC updater (no logic of its own)"
fresh
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;
        config) echo "tags: proxmox-gui"; echo "description: proxmox-gui-managed"; return 0 ;;
        exec) [[ "$*" == *"ip -4"* ]] && echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"; return 0 ;;
    esac
    return 0
H
inst --signers "$SIGNERS" --update --ctid 150 --release v0.0.1
assert_rc 0 "update"
assert_logged "pct exec 150 -- /usr/local/sbin/proxmox-gui-updater apply --tag v0.0.1" "updater invoked with the verified tag"
assert_not_logged "--allow-downgrade" "no downgrade by default"
assert_not_logged "pct push" "nothing pushed: the LXC downloads and re-verifies by itself"
inst --signers "$SIGNERS" --update --ctid 150 --release v0.0.1 --allow-downgrade
assert_logged "proxmox-gui-updater apply --tag v0.0.1 --allow-downgrade" "downgrade only when explicitly requested"
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;
        config) echo "tags: proxmox-gui"; echo "description: proxmox-gui-managed"; return 0 ;;
        exec) [[ "$*" == *"proxmox-gui-updater"* ]] && return 1; [[ "$*" == *"ip -4"* ]] && echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0"; return 0 ;;
    esac
    return 0
H
inst --signers "$SIGNERS" --update --ctid 150 --release v0.0.1
assert_rc_nonzero "updater failure propagates"
assert_contains "$ERR" "updater failed" "message"
fresh
inst --signers "$SIGNERS" --release v0.0.1 --allow-downgrade
assert_rc_nonzero "--allow-downgrade is update-only"

finish
