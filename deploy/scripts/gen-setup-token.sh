#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/scripts/gen-setup-token.sh — one-time first-run setup token (F-06, P3-07).
#
# POST /api/v1/setup/admin requires this token (X-Setup-Token), so whoever reaches
# the wizard first over the network cannot become admin without shell access to the
# LXC. 32 random bytes, url-safe base64; root:proxmox-gui 0440. Preserved if present.
# The value is never printed.

set -euo pipefail

ETC_DIR="${PGUI_ETC_DIR:-/etc/proxmox-gui}"
APP_GROUP="proxmox-gui"
TOKEN_PATH="${ETC_DIR}/setup-token"

if [[ -f "$TOKEN_PATH" ]]; then
    echo "setup-token already exists at $TOKEN_PATH (preserving)"
    exit 0
fi

mkdir -p "$ETC_DIR"
umask 077
tmp="$(mktemp -p "$ETC_DIR" .setup-token.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_' >"$tmp"
chown "root:${APP_GROUP}" "$tmp"
chmod 0440 "$tmp"
mv -f "$tmp" "$TOKEN_PATH"
trap - EXIT
echo "Wrote $TOKEN_PATH (32 random bytes, mode 0440, owner root:${APP_GROUP})"
