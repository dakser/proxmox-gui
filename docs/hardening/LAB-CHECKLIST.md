# Lista de validación en laboratorio (P7-05)

Esto es lo que **ningún test automático cubre**: requiere un PVE real. Hazlo en un PVE anidado o un nodo de
pruebas, con **snapshot previo** del nodo. Marca cada paso y anota el resultado real junto al esperado.

Convenciones: `$FORK` = `dakser/proxmox-gui`, `$V` = versión publicada y firmada (p. ej. `v0.1.0`), `$CT` = id del LXC creado.
Antes de empezar: `deploy/release-signers` con TU clave (HUMAN-TODO 1), release `$V` firmada y publicada.

## 0. Preparación

- [ ] Snapshot del nodo (o del PVE anidado).
- [ ] `ssh-keygen -Y verify` de `SHA256SUMS` con tu clave pública **antes** de instalar (flujo de `deploy/README.md`).
  Esperado: `Good "proxmox-gui-release" signature`.
- [ ] Guardar `cp ~/.ssh/authorized_keys /root/authorized_keys.before` en el host (para el paso 9).

## 1. Instalación sin community-scripts

- [ ] `bash install.sh --release $V` (descargado y verificado, no `curl | bash`).
  Esperado: verifica firma y hash **en el host** antes de crear nada; crea un LXC sin privilegios; imprime URL y token de setup.
- [ ] En el host: `diff ~/.ssh/authorized_keys /root/authorized_keys.before` → sin cambios.
  Esperado: no se instala ni la puerta SSH ni ninguna clave.
- [ ] `pct exec $CT -- systemctl is-active proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend redis-server caddy`
  → todo `active`.
- [ ] `pct exec $CT -- systemd-analyze security proxmox-gui-api.service` → puntuación ≤ la anotada en `LOG.md` (1.5 offline).
  Anota la puntuación real.
- [ ] `curl -k https://<ip>/api/v1/health` → 200.

## 2. Comprobaciones que dependen del kernel/LXC real (no simulables)

- [ ] **MemoryDenyWriteExecute** en los servicios Python y Node: los tres arrancan y sirven peticiones. Si Node/V8 falla con
  `MemoryDenyWriteExecute=yes`, es un hallazgo: anótalo y relaja solo ese servicio (registrar decisión).
- [ ] **RestrictAddressFamilies** en Node (frontend): la UI carga y el SSR llega a la API por su socket/loopback.
- [ ] **PrivateDevices** / `ProtectKernel*` en un LXC sin privilegios: ningún servicio queda en `failed`
  (`systemctl --failed` vacío). Si alguno falla por el contenedor, anotar cuál.
- [ ] `journalctl -p err -b` dentro del CT sin errores de sandboxing (`status=218/CAPABILITIES`, `226/NAMESPACE`, etc.).

## 3. Primer administrador

- [ ] Abrir la URL. `/setup` sin token → rechazado; con token erróneo → rechazado (y con límite de intentos).
- [ ] Con el token correcto crear el admin. Esperado: el token deja de valer; `/setup` responde ya "configurado".
- [ ] Consola del navegador: **cero violaciones CSP** en login, dashboard y setup.

## 4. Registrar un clúster

- [ ] Registrar el PVE de laboratorio con un API token (privilegios mínimos). Esperado: conecta, muestra nodos.
- [ ] Intentar registrar un destino no permitido por la política (loopback/metadatos/link-local). Esperado: rechazado.
- [ ] El token nunca aparece en respuestas de la API ni en el DOM (buscar en las herramientas de red).

## 5. VM y LXC

- [ ] Crear un LXC y una VM por el asistente. Esperado: `202`, el job aparece en el cajón de tareas y termina `succeeded`.
- [ ] Acciones de energía, snapshot, resize. Esperado: cada una es un job con UPID persistido.
- [ ] Usuario de otro equipo no ve los jobs/eventos del primero (A1–A5 del APP-REVIEW).

## 6. Consola

- [ ] Abrir la consola noVNC desde un clic (vncticket ~30–40 s). Esperado: conecta con la CSP nueva sin violaciones.
- [ ] Un ticket viejo (>60 s) falla limpiamente. El relay solo habla con el host del clúster registrado (pinning, F-13).

## 7. Backup y restauración

- [ ] Backup de la BD/config del propio portal (`proxmox-gui` + updater) y restaurarlo en un CT nuevo.
  Esperado: usuarios, clústeres y secretos descifran con la `master.key` restaurada.
- [ ] Backup/restore de una VM desde el portal contra el almacenamiento de laboratorio.

## 8. Community-scripts (instalación aparte)

- [ ] Reinstalar/actualizar con `--enable-community-scripts`. Esperado: se instala la puerta SSH con `from=` la IP **real**
  del LXC y `command=` fijo; una sola entrada marcada en `authorized_keys`.
- [ ] Desde otra IP, la clave no entra (`from=`). Desde el CT, un comando distinto del permitido es rechazado.
- [ ] Ejecutar un script de community-scripts: se ejecuta con `pct exec` **dentro del CT recién creado**, nunca en el host, con el
  commit fijado y la atribución visible. Comprobar que el host no cambia (`ps`, `/tmp`, `git status` de `/root`).
- [ ] Tag de script sin permiso → rechazado por la puerta.

## 9. Actualización N → N+1 y rollback

- [ ] Publicar `$V+1` firmada. Desde la UI y desde `install.sh --update`: actualiza; `status.json` pasa a `succeeded`;
  `/api/v1/health` responde; la BD se conserva.
- [ ] Publicar una release **rota a propósito** (p. ej. el API no arranca). Esperado: el updater detecta el fallo, hace rollback
  al release anterior y restaura la BD; `status.json` = `failed` con `rolled_back=true`; el portal vuelve a servir.
- [ ] Una release con firma inválida o hash alterado → rechazada antes de tocar nada.

## 10. Desinstalación

- [ ] `install.sh --uninstall` (sin `--purge`): el CT desaparece, datos del host intactos.
- [ ] `install.sh --uninstall --purge`: además elimina lo que instaló en el host.
- [ ] `diff ~/.ssh/authorized_keys /root/authorized_keys.before` → **idéntico** (la puerta SSH y su entrada desaparecen).
  `ls /usr/local/sbin | grep proxmox-gui` → vacío en el host.

## Al terminar

Restaurar el snapshot. Pasar los resultados reales (y cualquier desviación) a `HARDENING-REPORT.md` §3 o a un issue del fork.
