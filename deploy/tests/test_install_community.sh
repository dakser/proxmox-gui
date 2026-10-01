#!/usr/bin/env bash
# shellcheck shell=bash
# Opt-in SSH channel (F-01/F-11): install.sh --enable-community-scripts and
# --uninstall. Uses the real ssh-keygen; pct/pvesh/etc. are shims.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
INSTALL="$DEPLOY_DIR/install.sh"
AK="$PGUI_ROOT/root/.ssh/authorized_keys"
GATE="$PGUI_ROOT/usr/local/sbin/proxmox-gui-ssh-gate"

# Fake node host key + a GUI key pair "inside the CT".
mkdir -p "$T_TMP/hostkeys"
ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/hostkeys/host" -C root@node
ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/gui" -C "proxmox-gui@ct"
ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/gui2" -C "proxmox-gui@ct2"
PUBKEY_FILE="$T_TMP/ct-pub"
export PUBKEY_FILE

fresh() {  # fresh: clean state, signed release, an existing GUI CT 200 with IP 10.0.0.5
    reset_env
    make_release v0.0.1
    mkdir -p "$PGUI_ROOT/etc/ssh" "$PGUI_ROOT/root/.ssh" "$T_TMP/pushed"
    cp "$T_TMP/hostkeys/host.pub" "$PGUI_ROOT/etc/ssh/ssh_host_ed25519_key.pub"
    shim_handler pct <<'H'
        case "$1" in
            status) echo "status: running"; return 0 ;;
            config) echo "tags: proxmox-gui"; echo "description: proxmox-gui-managed"; return 0 ;;
            push) cp "$3" "$T_TMP/pushed/$(basename "$4")"; return 0 ;;
            exec)
                case "$*" in
                    *"ip -4"*) echo "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0" ;;
                    *"cat /etc/proxmox-gui/gui_ed25519.pub"*) cat "$PUBKEY_FILE" ;;
                esac
                return 0 ;;
        esac
        return 0
H
    shim_handler pvesh <<'H'
        return 0
H
}
enable() { run_cmd bash "$INSTALL" --signers "$SIGNERS" --update --ctid 200 --release v0.0.1 --host-ip 10.0.0.1 "$@"; }
initial_keys() { printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexisting admin@laptop\n' >"$AK"; chmod 600 "$AK"; }

test_case "without the flag authorized_keys and the gate are never touched"
fresh; initial_keys; before="$(cat "$AK")"
enable
assert_rc 0 "update without the flag"
assert_eq "$before" "$(cat "$AK")" "authorized_keys unchanged"
assert_no_file "$GATE" "gate not installed"

test_case "with the flag: restricted entry, gate installed, host key pinned"
fresh; initial_keys; cp "$T_TMP/gui.pub" "$PUBKEY_FILE"
enable --enable-community-scripts
assert_rc 0 "enable"
assert_file "$GATE" "gate installed"; assert_mode "$GATE" 755
assert_eq "2" "$(wc -l <"$AK")" "existing key kept + one new line"
line="$(grep 'proxmox-gui@200$' "$AK")"
kb="$(cut -d' ' -f2 "$T_TMP/gui.pub")"
assert_eq "restrict,from=\"10.0.0.5\",command=\"/usr/local/sbin/proxmox-gui-ssh-gate\" ssh-ed25519 $kb proxmox-gui@200" "$line" "exact restricted line"
assert_mode "$AK" 600
ssh-keygen -l -f "$AK" >/dev/null 2>&1 && _ok=1 || _ok=0; assert_eq 1 "$_ok" "authorized_keys parses"
kh="$(cat "$T_TMP/pushed/known_hosts")"
assert_contains "$kh" " ssh-ed25519 $(cut -d' ' -f2 "$T_TMP/hostkeys/host.pub")" "host key delivered for known_hosts"
assert_contains "$kh" "10.0.0.1" "host IP in known_hosts"
assert_logged "COMMUNITY_SCRIPTS_ENABLED=true" "feature switched on inside the CT"

test_case "re-running leaves exactly one line for the CT (idempotent)"
fresh; initial_keys; cp "$T_TMP/gui.pub" "$PUBKEY_FILE"
enable --enable-community-scripts; enable --enable-community-scripts
assert_eq "1" "$(grep -c 'proxmox-gui@200$' "$AK")" "one entry"
assert_eq "2" "$(wc -l <"$AK")" "no growth"

test_case "a rotated GUI key replaces the old line"
fresh; initial_keys; cp "$T_TMP/gui.pub" "$PUBKEY_FILE"; enable --enable-community-scripts
cp "$T_TMP/gui2.pub" "$PUBKEY_FILE"; enable --enable-community-scripts
assert_eq "1" "$(grep -c 'proxmox-gui@200$' "$AK")" "still one entry"
assert_contains "$(cat "$AK")" "$(cut -d' ' -f2 "$T_TMP/gui2.pub")" "new key present"
assert_not_contains "$(cat "$AK")" "$(cut -d' ' -f2 "$T_TMP/gui.pub")" "old key gone"

test_case "hostile public keys are rejected without writing authorized_keys"
kb="$(cut -d' ' -f2 "$T_TMP/gui.pub")"
i=0
for bad in \
    "ssh-ed25519 $kb"$'\n'"ssh-ed25519 $kb second" \
    "command=\"/bin/sh\" ssh-ed25519 $kb x" \
    "ssh-rsa $kb x" \
    "ssh-ed25519 AAAA-not*base64 x" \
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIbogus x" \
    "" \
    "no key here"; do
    i=$((i + 1))
    fresh; initial_keys; before="$(cat "$AK")"
    printf '%s\n' "$bad" >"$PUBKEY_FILE"
    enable --enable-community-scripts
    assert_rc_nonzero "hostile pubkey #$i"
    assert_eq "$before" "$(cat "$AK")" "hostile pubkey #$i: authorized_keys unchanged"
done

test_case "a pubkey comment is never copied (injection via comment)"
fresh; initial_keys
printf 'ssh-ed25519 %s evil,command="/bin/sh"\n' "$(cut -d' ' -f2 "$T_TMP/gui.pub")" >"$PUBKEY_FILE"
enable --enable-community-scripts
assert_rc 0 "comment ignored"
assert_not_contains "$(cat "$AK")" "/bin/sh" "comment not propagated"

test_case "uninstall round trip restores authorized_keys and removes the gate"
fresh; initial_keys; before="$(cat "$AK")"; cp "$T_TMP/gui.pub" "$PUBKEY_FILE"
enable --enable-community-scripts
run_cmd bash "$INSTALL" --uninstall --ctid 200
assert_rc 0 "uninstall"
assert_eq "$before" "$(cat "$AK")" "authorized_keys identical to the initial state"
assert_no_file "$GATE" "gate removed with the last entry"
assert_not_logged "pct destroy" "container kept without --purge"

test_case "uninstall keeps the gate while another CT still uses it"
fresh; initial_keys; cp "$T_TMP/gui.pub" "$PUBKEY_FILE"
enable --enable-community-scripts
run_cmd bash "$INSTALL" --signers "$SIGNERS" --update --ctid 201 --release v0.0.1 --host-ip 10.0.0.1 --enable-community-scripts
run_cmd bash "$INSTALL" --uninstall --ctid 200
assert_eq "0" "$(grep -c 'proxmox-gui@200$' "$AK")" "CT 200 revoked"
assert_eq "1" "$(grep -c 'proxmox-gui@201$' "$AK")" "CT 201 kept"
assert_file "$GATE" "gate kept"

test_case "--purge needs the typed CTID; wrong answer destroys nothing"
fresh; initial_keys
set +e; printf 'nope\n' | bash "$INSTALL" --uninstall --ctid 200 --purge >/dev/null 2>"$T_TMP/e"; RC=$?; set -e
assert_rc_nonzero "wrong confirmation"; assert_not_logged "pct destroy" "nothing destroyed"
set +e; bash "$INSTALL" --uninstall --ctid 200 --purge >/dev/null 2>&1 </dev/null; RC=$?; set -e
assert_rc_nonzero "no confirmation at all"; assert_not_logged "pct destroy" "nothing destroyed (EOF)"
set +e; printf '200\n' | bash "$INSTALL" --uninstall --ctid 200 --purge >/dev/null 2>&1; RC=$?; set -e
assert_rc 0 "confirmed purge"; assert_logged "pct destroy 200" "destroyed after confirmation"

test_case "--purge refuses a CT that is not ours"
fresh; initial_keys
shim_handler pct <<'H'
    case "$1" in
        status) echo "status: running"; return 0 ;;
        config) echo "tags: prod"; return 0 ;;
    esac
    return 0
H
set +e; printf '200\n' | bash "$INSTALL" --uninstall --ctid 200 --purge >/dev/null 2>&1; RC=$?; set -e
assert_rc_nonzero "foreign CT purge"; assert_not_logged "pct destroy" "foreign CT not destroyed"
assert_not_logged "pct stop" "foreign CT not stopped"

finish
