# Acciones que solo puede hacer una persona

1. **Clave de firma de releases (D2/D3).** Genera `ssh-keygen -t ed25519 -f ~/.ssh/proxmox-gui-release`, pon la línea pública en `deploy/release-signers` (formato `proxmox-gui-release namespaces="proxmox-gui-release" ssh-ed25519 AAAA…`) y ejecuta `scripts/sync-signers.sh`. Hasta entonces `install.sh` se niega a instalar y `scripts/check.sh` falla (salvo `PGUI_ALLOW_PLACEHOLDERS=1`).
