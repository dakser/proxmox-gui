#!/usr/bin/env bash
# shellcheck shell=bash
# Static checks of the shipped systemd units (F-02, F-09, F-12): privilege model and
# sandboxing directives must not regress.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
U="$DEPLOY_DIR/systemd"

has() { grep -Eq "^$2\$" "$U/$1"; }
check_has() { if has "$1" "$2"; then _report ok; else _report FAIL "$1 lacks: $2"; fi; }
check_lacks() { if has "$1" "$2"; then _report FAIL "$1 must not contain: $2"; else _report ok; fi; }

test_case "API and worker units: unprivileged, strict sandbox"
for unit in proxmox-gui-api.service proxmox-gui-worker.service; do
    for d in "User=proxmox-gui" "Group=proxmox-gui" "NoNewPrivileges=true" "PrivateTmp=true" "PrivateDevices=true" \
             "ProtectSystem=strict" "ProtectHome=true" "ReadWritePaths=/var/lib/proxmox-gui" "CapabilityBoundingSet=" \
             "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" "RestrictNamespaces=true" "LockPersonality=true" \
             "ProtectKernelTunables=true" "ProtectKernelModules=true" "ProtectKernelLogs=true" "ProtectControlGroups=true" \
             "ProtectClock=true" "SystemCallFilter=@system-service" "SystemCallArchitectures=native" "UMask=0077" \
             "MemoryDenyWriteExecute=true" "SupplementaryGroups=redis"; do
        check_has "$unit" "$d"
    done
    check_lacks "$unit" "User=root"
    check_lacks "$unit" "ProtectSystem=(full|true)"
    check_lacks "$unit" "ExecStartPre=.*"
done

test_case "frontend unit: separate user, no secrets, no Redis, JIT allowed"
f=proxmox-gui-frontend.service
check_has "$f" "User=proxmox-gui-web"
check_has "$f" "Group=proxmox-gui-web"
check_has "$f" "ProtectSystem=strict"
check_has "$f" "NoNewPrivileges=true"
check_has "$f" "CapabilityBoundingSet="
check_has "$f" "RestrictAddressFamilies=AF_INET AF_INET6"
check_has "$f" "InaccessiblePaths=-/etc/proxmox-gui -/var/lib/proxmox-gui -/run/redis"
check_lacks "$f" "MemoryDenyWriteExecute=true"
check_lacks "$f" "ReadWritePaths=.*"
check_lacks "$f" "User=(root|proxmox-gui)"
check_lacks "$f" "SupplementaryGroups=.*"
check_has "$f" "ExecStart=/opt/node/bin/node /opt/proxmox-gui/current/frontend/build/index.js"

test_case "no unit references sudo, and nothing runs code from a service-writable path"
for unit in "$U"/*.service "$U"/*/*.conf; do
    assert_not_contains "$(grep -v '^#' "$unit")" "sudo" "$(basename "$unit"): no sudo"
done
assert_eq "" "$(grep -E '^Exec' "$U"/*.service | grep '/var/lib/proxmox-gui' || true)" "no Exec* from the data directory"

test_case "no sudoers or visudo remains in deploy/"
assert_eq "" "$(grep -rIn --exclude-dir=tests --exclude='*.md' -E 'visudo|sudoers|sudo -n|NOPASSWD' "$DEPLOY_DIR" | grep -Ev '^[^:]+:[0-9]+:[[:space:]]*#' || true)" "no sudo machinery in deploy/ (code, ignoring comments)"

finish
