# Laboratorio en PVE anidado — guía de ejecución y tabla de resultados

Complementa a `LAB-CHECKLIST.md` (qué comprobar) con el **cómo** en un PVE anidado y con una tabla para anotar lo que
ocurre de verdad. Primero se hacen las secciones 0 a 3, y solo después el resto.

## 0. Antes de empezar

- **Snapshot a nivel de la máquina que aloja el PVE anidado** (desde el hipervisor exterior), con nombre `pre-pgui`.
  No basta con un snapshot del LXC: `install.sh` solo toca el host si activas community-scripts, y entonces
  modifica `authorized_keys` y `/usr/local/sbin`, cosa que un snapshot del CT no revierte.
- PVE 8.x con `pct`, `pvesh`, `curl`, `tar`, `sha256sum` y `ssh-keygen`, y salida HTTPS a `github.com`,
  `deb.debian.org`, `pypi.org` y `nodejs.org` desde el host **y** desde el LXC.
- Almacenamiento y puente por defecto (`local-lvm`, `vmbr0`); si no existen, pásalos con `--storage` y `--bridge`.
- Copia las claves del host: `cp ~/.ssh/authorized_keys /root/authorized_keys.before`.

## 1. Descargar, verificar e instalar (sin community-scripts)

En el host PVE, como root:

```bash
REPO=dakser/proxmox-gui; TAG=v0.1.0
mkdir -p ~/pgui && cd ~/pgui && rm -f *
for f in install.sh SHA256SUMS SHA256SUMS.sig; do curl -fsSLO "https://github.com/$REPO/releases/download/$TAG/$f"; done
printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "ssh-ed25519 AAAA...tu-clave-publica..." > allowed_signers
ssh-keygen -Y verify -f allowed_signers -I proxmox-gui-release -n proxmox-gui-release -s SHA256SUMS.sig < SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
less install.sh
bash install.sh --release "$TAG" --ctid 200 2>&1 | tee ~/pgui/install.log
```

La clave pública sale de tu gestor de contraseñas o de `~/.ssh/proxmox-gui-release.pub`, no de la propia release.
Usa un CTID libre; si no pasas `--ctid`, se toma el siguiente libre. Guarda `install.log` para anexarlo al informe.

## 2. Diagnóstico rápido si algo falla

Sustituye `200` por tu CTID.

```bash
pct exec 200 -- systemctl --failed
pct exec 200 -- systemctl is-active proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend redis-server caddy
pct exec 200 -- journalctl -p err -b --no-pager | tail -40
pct exec 200 -- journalctl -u proxmox-gui-frontend -b --no-pager | tail -60
pct exec 200 -- systemd-analyze security proxmox-gui-api.service | tail -3
```

Señales de sandboxing demasiado estricto: `status=218/CAPABILITIES`, `226/NAMESPACE` o fallos de
`mmap`/`W^X` en Node. Si un servicio falla por una directiva concreta, **no edites los ficheros de la release**
(son inmutables y root). Para probar, usa un drop-in solo de ese servicio y anótalo en la tabla:

```bash
pct exec 200 -- systemctl edit proxmox-gui-frontend    # p. ej. MemoryDenyWriteExecute=no
pct exec 200 -- systemctl restart proxmox-gui-frontend
```

Si una directiva hay que relajarla, el cambio real se hace en `deploy/systemd/` y sale en una release nueva.

## 3. Orden recomendado

1. Secciones 0 a 3 del checklist (instalación, kernel/LXC, primer admin). **Avísame con la tabla antes de seguir.**
2. Secciones 4 a 7 (clúster, VM/LXC, consola, backup).
3. Sección 10 (desinstalar con y sin `--purge`) y restaurar `pre-pgui`.
4. Sección 8 (community-scripts) en una **instalación aparte** con `--enable-community-scripts`, tras restaurar el snapshot.
5. Sección 9 (actualización y rollback) al final: necesita una release N+1 firmada. La `v0.1.1` puede servir de N+1.
   Para el caso de rollback hace falta una release rota a propósito: publícala solo para la prueba, márcala como
   pre-release y bórrala al terminar. No dejes publicada una release rota con tu firma.

## 4. Tabla de resultados

Una fila por comprobación del checklist. Anota lo que obtuviste, no lo que esperabas. `OK`, `FALLA` o `N/A`.

| Sección | Comprobación | Esperado | Obtenido | Estado | Notas / acción |
|---|---|---|---|---|---|
| 0 | Firma de `SHA256SUMS` | `Good "proxmox-gui-release" signature` | | | |
| 1 | `install.sh` verifica antes de crear nada | verifica y crea LXC sin privilegios | | | |
| 1 | `authorized_keys` sin cambios | `diff` vacío | | | |
| 1 | Cinco servicios activos | `active` ×5 | | | |
| 1 | `systemd-analyze security` (API) | ≤ 1.5 | | | |
| 1 | `/api/v1/health` | 200 | | | |
| 2 | `MemoryDenyWriteExecute` (Python y Node) | servicios arrancan y responden | | | |
| 2 | `RestrictAddressFamilies` en Node | la UI carga y el SSR llega a la API | | | |
| 2 | `PrivateDevices` / `ProtectKernel*` | `systemctl --failed` vacío | | | |
| 2 | `journalctl -p err -b` | sin errores de sandboxing | | | |
| 3 | `/setup` sin token y con token erróneo | rechazado, con límite de intentos | | | |
| 3 | Crear admin con el token correcto | el token deja de valer | | | |
| 3 | Consola del navegador | cero violaciones CSP | | | |
| 4 | Registrar clúster con token PVE de privilegios mínimos | conecta y lista nodos | | | |
| 4 | Destino no permitido (loopback, metadatos) | rechazado | | | |
| 4 | Token PVE en respuestas de la API y en el DOM | no aparece | | | |
| 5 | Crear LXC y VM | `202`, job `succeeded` | | | |
| 5 | Energía, snapshot, resize | cada uno es un job con UPID | | | |
| 5 | Aislamiento entre equipos | no ve jobs ni eventos ajenos | | | |
| 6 | Consola noVNC con CSP nueva | conecta sin violaciones | | | |
| 6 | Ticket viejo (>60 s) | falla limpiamente | | | |
| 7 | Backup/restauración del portal | usuarios, clústeres y secretos descifran | | | |
| 7 | Backup/restauración de una VM | restaura contra el almacenamiento de laboratorio | | | |
| 8 | Puerta SSH con `from=` y `command=` | una entrada marcada, solo desde la IP del LXC | | | |
| 8 | Script de community-scripts | corre con `pct exec` dentro del CT, nunca en el host | | | |
| 9 | Actualización N → N+1 | `succeeded`, BD conservada | | | |
| 9 | Release rota a propósito | rollback con BD restaurada, `rolled_back=true` | | | |
| 9 | Firma inválida o hash alterado | rechazada antes de tocar nada | | | |
| 10 | `--uninstall` y `--uninstall --purge` | `authorized_keys` idéntico al original | | | |

## 5. Al terminar

Restaura el snapshot, guarda `install.log` y esta tabla rellena en `docs/hardening/LAB-RESULTS.md` (o en un issue del
fork) y abre una tarea por cada `FALLA`. Cada una se corrige con un cambio pequeño, su prueba y una release nueva firmada.
