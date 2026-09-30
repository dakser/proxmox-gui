#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/scripts/gen-jwt-secret.sh — idempotent JWT secret + PAT pepper generator.
#
# Writes TWO files under /etc/proxmox-gui/, both root:proxmox-gui mode 0440
# (readable by the service, not replaceable by it — same model as master.key):
#
#   - jwt.secret  : 48 url-safe base64 chars. Signs the access JWT (D-10: 15-min TTL).
#   - pat.pepper  : 48 url-safe base64 chars. Per-deployment pepper mixed into PAT
#                   secret hashing (D-15).
#
# Existing files are preserved. Files are created with umask 077 into a temp file in
# the same directory and atomically renamed.

set -euo pipefail

ETC_DIR="${PGUI_ETC_DIR:-/etc/proxmox-gui}"
APP_GROUP="proxmox-gui"

# 36 random bytes -> 48 url-safe base64 chars.
gen_secret() {
    head -c 36 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_' | cut -c1-48
}

write_secret_file() {
    local path="$1" label="$2"
    if [[ -f "$path" ]]; then
        echo "$label already exists at $path (preserving)"
        return 0
    fi
    local tmp
    tmp="$(mktemp -p "$ETC_DIR" ".${label}.XXXXXX")"
    trap 'rm -f "$tmp"' RETURN
    gen_secret >"$tmp"
    chown "root:${APP_GROUP}" "$tmp"
    chmod 0440 "$tmp"
    mv -f "$tmp" "$path"
    echo "Wrote $path (48 url-safe base64 chars, mode 0440, owner root:${APP_GROUP})"
}

mkdir -p "$ETC_DIR"
chown "root:${APP_GROUP}" "$ETC_DIR"
chmod 0750 "$ETC_DIR"

umask 077
write_secret_file "${ETC_DIR}/jwt.secret" "jwt.secret"
write_secret_file "${ETC_DIR}/pat.pepper" "pat.pepper"
