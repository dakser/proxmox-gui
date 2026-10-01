#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/scripts/gen-master-key.sh — idempotent master.key generator (D-14).
#
# Writes 32 cryptographically-random bytes to /etc/proxmox-gui/master.key.
# The service user (proxmox-gui) reads it on boot to build the Fernet cipher that
# encrypts PVE API tokens and refresh tokens at rest (D-15).
#
# Ownership model (F-02/F-09): root:proxmox-gui, mode 0440 — the app can read the
# key but cannot replace it, and nobody else can read it. The parent directory is
# root:proxmox-gui 0750. app/core/cipher.py accepts group-read but rejects any
# other/group-write bit.
#
# Idempotent: an existing key is preserved (rotation: deploy/README.md).
# Created with umask 077 into a temp file and renamed, so the final path never
# exists with wider permissions. Nothing is written to stdout (Pitfall T-01-04-10).

set -euo pipefail

ETC_DIR="${PGUI_ETC_DIR:-/etc/proxmox-gui}"
KEY_PATH="${ETC_DIR}/master.key"
APP_GROUP="proxmox-gui"

if [[ -f "$KEY_PATH" ]]; then
    echo "master.key already exists at $KEY_PATH (preserving)"
    exit 0
fi

mkdir -p "$ETC_DIR"
chown "root:${APP_GROUP}" "$ETC_DIR"
chmod 0750 "$ETC_DIR"

umask 077
tmp="$(mktemp -p "$ETC_DIR" .master.key.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
head -c 32 /dev/urandom >"$tmp"
chown "root:${APP_GROUP}" "$tmp"
chmod 0440 "$tmp"
mv -f "$tmp" "$KEY_PATH"
trap - EXIT

echo "Wrote $KEY_PATH (32 random bytes, mode 0440, owner root:${APP_GROUP})"
