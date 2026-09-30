#!/usr/bin/env bash
# shellcheck shell=bash
# Caddyfile rendering at boot (F-12): current IP or FQDN, validated before replacing.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
RENDER="$DEPLOY_DIR/lxc/render-caddyfile.sh"
OUTF="$PGUI_ROOT/etc/caddy/Caddyfile"

setup_tree() {
    reset_env
    mkdir -p "$PGUI_ROOT/opt/proxmox-gui/current/deploy/caddy" "$PGUI_ROOT/etc/proxmox-gui" "$PGUI_ROOT/etc/caddy"
    cp "$DEPLOY_DIR/caddy/Caddyfile.template" "$PGUI_ROOT/opt/proxmox-gui/current/deploy/caddy/"
}
render() { run_cmd env "PGUI_RENDER_IP=${RIP:-}" bash "$RENDER"; }

test_case "renders the current IP; a changed IP regenerates the file"
setup_tree
RIP=10.0.0.5 render
assert_rc 0 "render"
assert_contains "$(cat "$OUTF")" "https://10.0.0.5:443 {" "site address"
assert_not_contains "$(cat "$OUTF")" "__SITE_ADDR__" "placeholder replaced"
RIP=192.168.1.77 render
assert_contains "$(cat "$OUTF")" "https://192.168.1.77:443 {" "new IP rendered"
assert_not_contains "$(cat "$OUTF")" "10.0.0.5" "old IP gone"
assert_mode "$OUTF" 644

test_case "FQDN from site.env wins over the IP"
setup_tree
echo "PGUI_FQDN=gui.example.org" >"$PGUI_ROOT/etc/proxmox-gui/site.env"
RIP=10.0.0.5 render
assert_rc 0 "render"
assert_contains "$(cat "$OUTF")" "https://gui.example.org {" "fqdn site"

test_case "hostile site.env content is never interpolated"
setup_tree
printf 'PGUI_FQDN=a.b} { respond "x"\n' >"$PGUI_ROOT/etc/proxmox-gui/site.env"
RIP=10.0.0.5 render
assert_contains "$(cat "$OUTF")" "https://10.0.0.5:443 {" "falls back to the IP"
assert_not_contains "$(cat "$OUTF")" 'respond "x"' "no injection"
printf 'PGUI_FQDN=a..b\n' >"$PGUI_ROOT/etc/proxmox-gui/site.env"
rm -f "$OUTF"; RIP=10.0.0.5 render
assert_rc_nonzero "a..b rejected"; assert_no_file "$OUTF" "nothing written"

test_case "non-IPv4 override is rejected"
setup_tree
for ip in "1.2.3" "a.b.c.d" "10.0.0.5; rm" "1.2.3.4|x"; do
    rm -f "$OUTF"; RIP="$ip" render
    assert_rc_nonzero "ip '$ip'"; assert_no_file "$OUTF" "no file for '$ip'"
done

test_case "a template failing caddy validate keeps the previous Caddyfile"
setup_tree
RIP=10.0.0.5 render
before="$(cat "$OUTF")"
shim_handler caddy <<'H'
    return 1
H
RIP=10.0.0.9 render
assert_rc_nonzero "validate failure"
assert_eq "$before" "$(cat "$OUTF")" "previous Caddyfile kept"
assert_eq "0" "$(find "$PGUI_ROOT/etc/caddy" -name 'Caddyfile.*' | wc -l)" "no temp file left behind"

test_case "the template carries the hardening bits"
t="$(cat "$DEPLOY_DIR/caddy/Caddyfile.template")"
assert_contains "$t" "request_body" "body size limit"
assert_contains "$t" "max_size" "body size limit value"
assert_contains "$t" "read_header" "slow-client timeouts"
assert_contains "$t" "Strict-Transport-Security" "HSTS kept"
assert_contains "$t" "X-Content-Type-Options" "nosniff kept"
assert_contains "$t" "frame-ancestors 'self'" "clickjacking CSP kept"

finish
