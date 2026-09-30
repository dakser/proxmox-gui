#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/lxc/render-caddyfile.sh — installed as /usr/local/sbin/proxmox-gui-caddy-render (root).
# Run by proxmox-gui-caddy-render.service before every Caddy start.
#
# Renders /etc/caddy/Caddyfile from the current release's template:
#   - site address = https://<FQDN> if PGUI_FQDN is set in /etc/proxmox-gui/site.env,
#     otherwise https://<current IPv4 of eth0>:443 (so `tls internal` always has a
#     concrete SAN, even after a DHCP change);
#   - the result is validated with `caddy validate` when the binary is present and only
#     then atomically moved into place; on failure the previous Caddyfile is kept.
#
# PGUI_ROOT (tests only) prefixes filesystem paths. PGUI_RENDER_IP (tests only) overrides
# the detected address.

set -euo pipefail

ROOT="${PGUI_ROOT:-}"
TEMPLATE="${ROOT}/opt/proxmox-gui/current/deploy/caddy/Caddyfile.template"
SITE_ENV="${ROOT}/etc/proxmox-gui/site.env"
OUT="${ROOT}/etc/caddy/Caddyfile"

[[ -f "$TEMPLATE" ]] || { echo "ERROR: template not found: $TEMPLATE" >&2; exit 1; }

PGUI_FQDN=""
if [[ -f "$SITE_ENV" ]]; then
    # Only a plain PGUI_FQDN=<hostname> line is honoured (never sourced).
    PGUI_FQDN="$(sed -n 's/^PGUI_FQDN=\([A-Za-z0-9.-]\{1,253\}\)$/\1/p' "$SITE_ENV" | head -1)"
fi

if [[ -n "$PGUI_FQDN" ]]; then
    [[ "$PGUI_FQDN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ && "$PGUI_FQDN" != *..* ]] || { echo "ERROR: invalid PGUI_FQDN" >&2; exit 1; }
    SITE="https://${PGUI_FQDN}"
else
    ADDR="${PGUI_RENDER_IP:-}"
    if [[ -z "$ADDR" ]]; then
        ADDR="$(ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)"
    fi
    if [[ -z "$ADDR" ]]; then
        ADDR="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    [[ "$ADDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "ERROR: could not determine the LXC IPv4 address" >&2; exit 1; }
    SITE="https://${ADDR}:443"
fi

# Host allow-list for the API (TrustedHostMiddleware): the name/IP Caddy serves plus loopback.
# Written for the API/worker units (EnvironmentFile) to a root-owned runtime file.
if [[ -n "$PGUI_FQDN" ]]; then HOSTS="\"${PGUI_FQDN}\""; else HOSTS="\"${ADDR}\""; fi
RUNDIR="${ROOT}/run/proxmox-gui"
mkdir -p "$RUNDIR"
chmod 0755 "$RUNDIR"
printf 'PROXMOX_GUI_ALLOWED_HOSTS=[%s,"localhost","127.0.0.1"]\n' "$HOSTS" >"${RUNDIR}/site.env.new"
chmod 0644 "${RUNDIR}/site.env.new"

mkdir -p "$(dirname "$OUT")"
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
sed "s|__SITE_ADDR__|${SITE}|" "$TEMPLATE" >"$tmp"
if grep -q '__SITE_ADDR__' "$tmp"; then echo "ERROR: placeholder left in rendered Caddyfile" >&2; exit 1; fi
if command -v caddy >/dev/null 2>&1; then
    # `caddy validate` provisions the site's internal CA, which writes under $XDG_DATA_HOME
    # (HOME is unset in this unit and the root filesystem is read-only), so give it a scratch
    # directory in the runtime dir; nothing it writes there is used by the real server.
    vdir="$(mktemp -d "${RUNDIR}/validate.XXXXXX")"
    trap 'rm -f "$tmp"; rm -rf "$vdir"' EXIT
    HOME="$vdir" XDG_DATA_HOME="$vdir/data" XDG_CONFIG_HOME="$vdir/config" \
        caddy validate --config "$tmp" --adapter caddyfile >&2 \
        || { echo "ERROR: rendered Caddyfile failed 'caddy validate'; keeping the previous one" >&2; exit 1; }
    rm -rf "$vdir"
fi
chmod 0644 "$tmp"
mv -f "$tmp" "$OUT"
mv -f "${RUNDIR}/site.env.new" "${RUNDIR}/site.env"
trap - EXIT
echo "Rendered $OUT for ${SITE}"
