# Actualizaciones seguras (F-03, F-04, F-05)

Sin sudo, sin que el worker ejecute nada: el worker (usuario `proxmox-gui`) **solo escribe una solicitud**; un servicio de root
disparado por systemd hace todo el trabajo y solo acepta releases firmadas por la clave del dueño del fork.

## Piezas

| Pieza | Propietario | Función |
|---|---|---|
| `/var/lib/proxmox-gui/update/request` | escribe `proxmox-gui` | Solicitud: un tag `vX.Y.Z`, ≤ 64 bytes |
| `proxmox-gui-updater.path` | root | `PathExists=` sobre la solicitud → lanza el servicio |
| `proxmox-gui-updater.service` | root | `Type=oneshot`, `proxmox-gui-updater apply` |
| `/usr/local/sbin/proxmox-gui-updater` | root 0755 | `apply`, `rollback`, `status` |
| `/etc/proxmox-gui/release.conf` | root 0644 | `REPO_URL=https://github.com/<owner>/<repo>` (fijado por el instalador; nunca el upstream por defecto) |
| `/etc/proxmox-gui/release-signers` | root 0644 | `allowed_signers` (ancla de confianza; la entrega `install.sh`) |
| `/etc/proxmox-gui/pins.env` | root 0644 | Toolchain instalado (Node/Python); si una release lo cambia, el updater se detiene (reinstalar) |
| `/run/proxmox-gui-updater/status.json` | root 0644 | Estado legible por la app (el directorio es de root) |

## Flujo de `apply`

1. Lee la solicitud (≤ 64 bytes, `^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$`, sin `..`) y **la borra antes de hacer nada más**.
2. Política de versión: solo hacia adelante (semver; una release sin sufijo es mayor que su prerelease). Igual al activo → `noop`.
   Menor → rechazo, salvo `--allow-downgrade` en la consola de root (la solicitud del worker nunca puede pedirlo).
3. Descarga `SHA256SUMS`, `SHA256SUMS.sig` y `proxmox-gui-<tag>.tar.gz` de `<REPO_URL>/releases/download/<tag>/` a un directorio
   temporal de root (límites de tamaño). Verifica la firma (`ssh-keygen -Y verify`, namespace `proxmox-gui-release`) y el hash.
4. Valida el tarball **antes** de extraer (Python `tarfile`, sin ejecutar nada): rutas absolutas, `..`, enlaces simbólicos con destino
   absoluto o fuera del árbol, enlaces duros, dispositivos, FIFOs, nombres con caracteres de control. Extrae con
   `tar --no-same-owner --no-same-permissions` en `releases/<tag>` (directorio nuevo; **nunca** el activo ni el anterior).
5. Comprueba estructura (`backend/`, `frontend/build/index.js`, `deploy/`, `backend/requirements.lock`, `deploy/pins.env`) y toolchain.
6. `root:root`, modos 755/644. Venv nuevo con Python fijado; dependencias con `--require-hashes --only-binary=:all:`; backend no editable.
7. Backup consistente `sqlite3 .backup` → `/var/lib/proxmox-gui/backups/` (conserva los 5 últimos).
8. Migraciones con `runuser -u proxmox-gui` usando el venv nuevo (marca "migración iniciada" antes de ejecutarlas).
9. Cambio atómico del symlink `current` (`previous` apunta al release anterior); instala unidades, render de Caddy y updater desde el release nuevo.
10. Reinicia API y espera `GET /api/v1/health` hasta 60 s; después frontend; escribe `succeeded`; espera 5 s y reinicia el worker el último.
11. **Fallo en cualquier paso** ⇒ rollback: symlink al release previo, restaurar la BD **solo si la migración llegó a iniciarse**, reiniciar servicios,
    borrar el release fallido, estado `failed` (`rolled_back: true`).
12. Retención: `current` + `previous` + 1 más (los 3 más recientes por versión; nunca se borra el activo ni el previo).

## `status.json`

```json
{"state":"running|succeeded|failed|noop","step":"downloading","target":"v0.7.1","from":"v0.7.0","message":"…","rolled_back":false,"updated_at":"2026-09-30T12:00:00Z"}
```
`state` en curso = `running` (con `step`); terminales: `succeeded`, `failed`, `noop`. El mensaje es ASCII sin comillas ni barras.

## Consola de root

`proxmox-gui-updater apply --tag vX.Y.Z [--allow-downgrade]`, `proxmox-gui-updater rollback [--restore-db]`, `proxmox-gui-updater status`.
`install.sh --update` (host) verifica la release en el host y llama a `apply --tag` dentro del LXC.

## Límites

- No cambia Node/Python: si `deploy/pins.env` de la release difiere del instalado, se detiene con un mensaje claro (reinstalar con `install.sh`).
- La clave de firma solo se rota a mano (`/etc/proxmox-gui/release-signers`) o reinstalando.
- Una app comprometida puede pedir cualquier release **firmada y posterior**: puede forzar una actualización, no instalar código propio.
