# Hallazgos de seguridad — proxmox-gui

Base auditada: commit `e485bc4371e564d240993e0f98d272d41ff4124c` (2026-06-13), versión `0.6.3`.
Alcance: `deploy/` completo (install.sh, bootstrap.sh, update.sh, gen-*.sh, unidades systemd, Caddyfile.template),
más las partes del backend que tocan SSH, self-update, setup, Redis/arq y TLS.

**Qué NO se hizo en esta auditoría:** no se ejecutó la suite de tests, no se probó el instalador en un PVE real,
y el frontend (`frontend/src`, `frontend/build`) y los módulos de negocio del backend (quotas, teams, inventory,
lifecycle) no se revisaron línea por línea. Los ítems marcados **[VERIFICAR]** salen de lectura estática y deben
confirmarse con un test o en el laboratorio antes de darlos por ciertos.

Severidad: Alta = escalada a root en el nodo PVE o en el LXC, o instalación de código no autenticado.
Media = requiere una condición previa (app comprometida, operador engañado). Baja = higiene y robustez.

## Correcciones a la revisión previa (chat)

1. **"pat.pepper no se genera": falso.** `gen-jwt-secret.sh` genera `jwt.secret` y `pat.pepper`. Sin acción.
2. **El wrapper SSH propuesto era incorrecto para esta app.** Sugerí una allowlist de `pct list/status/start/stop` y
   excluir `pct exec`. Pero la función "community-scripts" existe justamente para hacer `pct exec` por SSH
   (`connector.py::_ssh_pct_exec`). El diseño correcto está en F-01 y en la fase P1 del plan.
3. **El self-update es peor de lo que se dijo.** No es solo que falte el sudoers: el worker ejecuta
   `bash update.sh` directamente como `proxmox-gui`, sin `sudo` (ver F-03).
4. **"Worker no se reinicia":** en la ruta de la UI es intencional (se reinicia al final). Solo la ruta
   `install.sh --update` lo deja con código viejo. Baja.

## Alta

### F-01 — El LXC obtiene SSH como root, sin restricciones, hacia el nodo PVE
- Evidencia: `install.sh` líneas 310-362 (añade la clave a `/root/.ssh/authorized_keys` sin opciones);
  `bootstrap.sh` 276-287 (clave privada `0400`, propietario `proxmox-gui`, sin passphrase);
  `connector.py::_ssh_pct_exec` (ejecuta `pct exec <vmid> -- <argv>` con `root@<node>`).
- Impacto: cualquier RCE en API, worker, Node o una dependencia lee la clave y obtiene root en el nodo PVE
  (y en el resto del clúster si hay confianza SSH entre nodos).
- Lo que la app realmente necesita del host: (a) `pct list` para el preflight, (b) `pct exec <vmid> -- <argv>`
  sobre contenedores que ella misma creó. Nada más. `pct exec` en un LXC sin privilegios queda acotado al contenedor.
- Corrección: opt-in (`--enable-community-scripts`, apagado por defecto); entrada `authorized_keys` con
  `restrict,from="<IP>",command="<gate>"`; gate propiedad de root con protocolo estructurado (sin shell),
  validación de vmid, marca/tag del CT, rechazo de CT privilegiados; validación de la clave pública;
  revocación al desinstalar. Ver P1.

### F-02 — Root ejecuta e instala archivos que el usuario de servicio puede modificar
- Evidencia: `bootstrap.sh` 247 y 256 (`chown -R proxmox-gui` sobre todo el release, incluidos `deploy/systemd`
  y `deploy/scripts`); root instala las unidades desde ahí (`bootstrap.sh` 116-121 y 377-382, `update.sh` 127-132);
  root ejecuta `gen-master-key.sh` y `gen-jwt-secret.sh` desde ese árbol (`bootstrap.sh` 265-266);
  instalación editable `pip install -e` (`bootstrap.sh` 338, `update.sh` 105) mantiene el código escribible.
- Impacto: un atacante con el usuario de servicio edita una unidad (`User=root`, `ExecStartPre=`) o un script y
  espera la siguiente ejecución de bootstrap/update para llegar a root dentro del LXC.
- Corrección: código, venv y unidades `root:root` de solo lectura; instalación no editable; el usuario de servicio
  escribe únicamente en `/var/lib/proxmox-gui`. Ver P3.

### F-03 — El self-update desde la UI no puede funcionar y, corregido a medias, sería una escalada
- Evidencia: `selfupdate_functions.py::run_self_update` invoca `bash <update.sh>` como el usuario del worker, sin sudo.
  `update.sh` necesita root (`chown`, `install` en `/etc/systemd/system`, `runuser`, `systemctl daemon-reload`).
  El sudoers (`bootstrap.sh` 314) solo permite tres `systemctl restart`. El worker tiene `NoNewPrivileges=true`
  (`proxmox-gui-worker.service`), lo que bloquea `sudo` de todos modos. `sudo` no se instala en `bootstrap.sh`
  **[VERIFICAR]**: si la plantilla no lo trae, `visudo -cf` falla y el bootstrap aborta (`bootstrap.sh` 318-325).
  Además `_locate_update_sh` extrae y ejecuta el `update.sh` que viene dentro del tarball.
- Impacto: la función no opera; la "solución" obvia (NOPASSWD para update.sh) sería escalada directa a root.
- Corrección: updater propiedad de root, disparado por una unidad `.path` de systemd que solo lee una versión
  validada; el worker solo pide la actualización y consulta un archivo de estado. Sin sudoers. Ver P4.

### F-04 — No hay autenticidad de las versiones que se instalan
- Evidencia: `install.sh` 66-67 y 306 (`curl .../raw/master/... | bash` dentro del LXC, rama mutable);
  `bootstrap.sh` 238 (`git clone --branch "$RELEASE"`, que no acepta SHAs) — dos descargas distintas del mismo
  ref con ventana de TOCTOU; `selfupdate/service.py` 28-55: el `sha256` viene del mismo origen que el tarball
  (el propio código lo acepta como riesgo T-05-04-09); el manifest por defecto apunta al repo del autor original
  (`github.com/chloepriceless/proxmox-gui/releases`), no a tu fork.
- Impacto: quien controle el repo, la cuenta o el ref ejecuta código como root en el host (install.sh) y en el LXC.
  Con un fork sin cambiar esa URL, tu instalación se actualizaría con código del upstream.
- Corrección: releases firmadas por ti (`ssh-keygen -Y`), verificación en host y en LXC con clave pública fijada,
  manifest y URL parametrizados a tu fork, descarga a archivo antes de ejecutar, versiones monótonas. Ver P2, P4, P6.

### F-05 — update.sh puede borrar el release activo y valida mal sus entradas
- Evidencia: `update.sh` 66 (`rm -rf "$TARGET_DIR"`) con `TARGET_DIR=releases/$RELEASE_TAG`. `install.sh --update`
  usa por defecto `RELEASE=master`, y el primer release se llama `master` (`bootstrap.sh` 48, 240): el update borra
  el código que está corriendo y, si algo falla después, no hay rollback en esa ruta. `RELEASE_TAG` no se valida
  (`.` o `..` afectan `releases/`). Python equivocado: `update.sh` 94-98 busca `${APP_HOME}/.venv/bin/python`
  (no existe en instalaciones nuevas) y cae en `python3` del sistema (3.11), pero el proyecto pide `>=3.12`.
  Migraciones antes del swap (línea 109-119) sin backup en la ruta `install.sh --update`. `tar` como root sin
  `--no-same-owner`/validación de rutas.
- Corrección: regex estricta del tag, jamás borrar el release activo, Python del venv actual, backup y rollback
  automáticos, extracción segura. Ver P4.

### F-06 — El asistente `/setup` crea el admin sin autenticación
- Evidencia: `setup/routes.py` (`POST /api/v1/setup/admin`, sin dependencia de auth; se cierra solo cuando ya hay
  admin). El banner de instalación anuncia `https://<IP>/setup`.
- Impacto: quien llegue primero por red tiene control total, incluidos los tokens de PVE que registre después.
- Corrección: token de un solo uso generado en la instalación, exigido por el endpoint (comparación en tiempo
  constante), límite de tasa, y el wizard lo pide. Ver P3/P5.

## Media

### F-07 — Inyección por interpolación y validación de entradas en los scripts de root
- `bootstrap.sh` 99-103 y 343-347 interpolan `RELEASE`/rutas dentro de `bash -c "..."` con comillas dobles; git admite
  `'`, `;` y `$` en nombres de ref, así que `$(...)` se expande en el shell de root antes de `runuser`. Poco probable
  (exige un ref malicioso) pero trivial de cerrar. `install.sh`: flags sin valor (`--cpu` al final) rompen con
  `set -u`; ningún valor numérico se valida; `--rootfs "$STORAGE:$DISK_GB"` acepta cualquier cosa.

### F-08 — `install.sh` trata cualquier CTID existente como propio y tiene bugs de robustez
- `install.sh` 141-163: cualquier CT existente entra por la ruta de update y ejecuta código descargado como root
  dentro de él, sin comprobar un marcador. `CTID` como variable de entorno es genérica.
- `install.sh` 71: `HOSTNAME` la define bash automáticamente, así que el LXC hereda el hostname del nodo PVE.
- `install.sh` 110 dice que la rama por defecto es `main`; el código usa `master`.
- `pveam download ... || true` y `2>/dev/null || true` (líneas 227, 344-345) ocultan errores reales.

### F-09 — Un solo usuario para todo, Redis sin autenticación y arq con pickle
- API, worker y frontend Node corren como `proxmox-gui` y todos pueden leer `master.key`, `jwt.secret` y
  `gui_ed25519`: un RCE en una dependencia del frontend equivale a comprometer todo.
- `worker.py` 172 y `main.py` 108-113: `RedisSettings(host="127.0.0.1", port=6379)` sin contraseña; no hay
  `job_serializer`/`job_deserializer`, así que arq usa pickle. Cualquier proceso local que pueda escribir en Redis
  ejecuta código como el worker.
- Corrección: usuario separado para el frontend sin acceso a secretos; Redis por socket Unix con permisos o con
  `requirepass` desde archivo; serializador JSON. Ver P3/P5.

### F-10 — Cadena de suministro del toolchain y del frontend
- `bootstrap.sh` 180-181: `curl -LsSf https://astral.sh/uv/install.sh | sh` como root, sin versión ni hash.
- `bootstrap.sh` 335-336 y `update.sh` 102-103: `pip install --upgrade pip setuptools wheel` sin fijar versión;
  `pyproject.toml` fija dependencias con `==` (bien) pero no hay lockfile con hashes ni `--require-hashes`.
- Node 18 (el de Debian 12) está fuera de soporte; `bootstrap.sh` instala `nodejs npm` de apt.
- `frontend/build/` está versionado (2206 archivos, 58 MB): no se puede verificar que el JS ejecutado salga de
  `frontend/src`. `frontend/pnpm-lock.yaml` sí existe.
- No existe `.github/`: no hay CI, ni escaneo, ni Dependabot.
- Corrección: pins con hash, build en CI, release firmada, Node 22 LTS. Ver P3/P6.

### F-11 — Cliente SSH sin identidad explícita y con TOFU en un directorio escribible
- `connector.py::_ssh_pct_exec` y `networks/preflight.py::_run_ssh_probe` invocan `ssh` sin `-i`, sin
  `IdentitiesOnly`, sin `UserKnownHostsFile`, con `StrictHostKeyChecking=accept-new`. La clave vive en
  `/etc/proxmox-gui/gui_ed25519`, pero no se le pasa a ssh **[VERIFICAR]**: puede que la función nunca haya
  funcionado sin una config externa (el README marca la UAT operativa como pendiente). El `known_hosts` cae en el
  HOME del usuario (`/opt/proxmox-gui`), que hoy es escribible por la app.
- Corrección: `-i`, `IdentitiesOnly=yes`, `UserKnownHostsFile` en `/var/lib/proxmox-gui/ssh/`, host key del nodo
  fijada por el instalador y `StrictHostKeyChecking=yes`. Ver P1/P5.

### F-12 — Endurecimiento incompleto de systemd, Caddy y la superficie web
- Unidades: `ProtectSystem=full` (no `strict`), sin `CapabilityBoundingSet`, `RestrictAddressFamilies`,
  `SystemCallFilter`, `ProtectKernel*`, `LockPersonality`, `RestrictNamespaces`, etc. (En el frontend Node no usar
  `MemoryDenyWriteExecute`, el JIT lo necesita.)
- Caddy: la IP del LXC queda fija en el sitio (`bootstrap.sh` 389-397); si DHCP la cambia, el TLS deja de servir.
  CSP con `'unsafe-inline'` en `script-src`. HSTS sobre certificado interno.
- `main.py` 183-189: `/api/docs`, `/api/redoc` y `/api/openapi.json` públicos; `TODO` de `TrustedHostMiddleware`.
- WebSockets (`/api/v1/ws/jobs`, `/api/v1/ws/console/...`): no se encontró validación de `Origin` **[VERIFICAR]**.

### F-13 — Puntos de diseño a verificar en el backend
- `console/proxy.py` 83-97: con `verify_ssl=False` usa `CERT_NONE`; el pinning por fingerprint se aplica en el
  conector REST (`connector.py` 111-115) pero hay que confirmar que el relay de consola también lo aplica **[VERIFICAR]**.
- Registro de clústeres: comprobar SSRF (host/puerto arbitrarios desde un admin) y que las respuestas de PVE no se
  reflejen sin sanear **[VERIFICAR]**.
- `master.key` y la base de datos viven en el mismo host y son legibles por el mismo usuario: comprometer la app
  permite descifrar los tokens de PVE. Es inherente al diseño; se mitiga con tokens privsep por tenant (ya previstos)
  y con permisos `root:proxmox-gui 0440`. Documentar como riesgo residual.
- `provisioning_functions.py` construye `bash -c "$(curl -fsSL <url>)"` para community-scripts: el código dice que
  el slug y la URL están validados y anclados a un commit; confirmar con un test que un slug hostil no llega al shell.

## Hallazgos de la revisión independiente de P0–P4 (segunda pasada)

### F-15 — El updater de root opera dentro de directorios que controla la app (Alta)
`/usr/local/sbin/proxmox-gui-updater` corre como root pero lee/escribe en `/var/lib/proxmox-gui/{update,backups}`, que
pertenecen a `proxmox-gui` (el usuario que se asume comprometible).
- Symlink/TOCTOU: la app puede reemplazar `backups/`, `update/` o un fichero dentro por un enlace simbólico; `chown`, `cp -f`
  (`${DB_FILE}.restore`) y `sqlite3 .backup` de root siguen el enlace → escritura/chown/chmod arbitrarios como root.
- `request`: se comprueba `-f && ! -L` y luego se lee (ventana TOCTOU); abrir una sola vez con `O_NOFOLLOW`/fd.
- `status.json` y el mensaje de solicitud inválida reflejan datos controlados por la app; sanear y limitar longitud.
**Mitigación:** ver P4-06 (directorios de trabajo de root fuera del alcance de la app: `/var/lib/proxmox-gui-updater`
`root:root 0700` para backups y staging; sólo `update/request` en zona app y leído con fd único, sin seguir enlaces;
restore mediante copia a fichero creado por root con `install`, nunca `cp -f` sobre rutas de la app).

### F-16 — Gate SSH del nodo: residuos (Media)
- `pct config` del CT se lee sin confinar el origen (la tag `proxmox-gui` la puede poner cualquier admin PVE: riesgo residual a documentar).
- El timeout envía `TERM` al grupo y no escala a `KILL`.
- Los argumentos/comandos rechazados se registran sin escapar (inyección de líneas en el log).
**Mitigación:** P1-08.

### F-17 — Diferencial validador/extractor del tarball (Media)
`validate_tarball` (Python `tarfile`) y `tar -xzf` (GNU) pueden interpretar distinto ciertos miembros (rutas duplicadas,
hardlinks a miembros previos, `./` y `..` normalizados, PAX/GNU long names). **Mitigación:** extraer con el mismo
intérprete que valida (Python, `filter='data'`, sin symlinks/hardlinks/dispositivos) o rechazar todo tipo de miembro distinto
de fichero regular/directorio; ver P4-06.

## Baja

### F-14 — Higiene
- `.planning/` (130+ archivos) contiene notas operativas del autor original: revisar con un escáner de secretos y
  de hostnames/IPs antes de publicar el fork. Historial git (335 commits): escanear con gitleaks.
- Licencia MIT y autoría original (`Bikini Bottom Capital GmbH`) deben conservarse en el fork.
- No hay desinstalador; el bootstrap deja `/opt/proxmox-gui-src` sin borrar.
- `pip`/`setuptools`/`wheel` con `--upgrade` no determinista; `apt-get install -y -qq` sin `--no-install-recommends`.

## Lo que ya está bien (no tocar)
LXC sin privilegios con `nesting`+`keyctl`; `set -euo pipefail` y trap de errores; variables hacia `pct exec` por
`env` (sin inyección en `install.sh`); dependencias Python fijadas con `==`; `master.key` 0400 con verificación de
permisos al arrancar; JWT corto + refresh rotativo en BD; PAT con pepper; cookies `httpOnly`/`Secure`/`SameSite`;
CSRF de doble envío; rate limiting con lista de proxies confiables; Redis en loopback; `admin off` en Caddy;
TLS pinning por fingerprint en el conector REST; `shlex.quote` en `lxc_exec`; catálogo de community-scripts anclado
a un commit; swap atómico con `ln -sfn`; venv por release; snapshot WAL-safe de la BD antes de actualizar.

### F-18 — Validadores con `re.match` + `$` aceptan un salto de línea final (Media) — *hallado en P5-07*
`_SLUG_RE`, `_COMMIT_SHA_RE` (community-scripts: se interpolan en una URL y en un `bash -c`), `_TAG_RE` del schema de self-update,
`_TOKEN_USER_RE`, `PVE_TAG_RE`, `_DISK_KEY_RE`, `_PAT_BEARER_RE` y `_LINUX_USERNAME_RE` (cloud-init: se escribe en YAML) usaban
`pattern.match(x)` con `$`; en Python `$` casa antes de un `\n` final, así que `"docker\n"` o `"<sha>\n"` pasaban la validación.
Evidencia: `tests/test_community_script_command.py` y `tests/test_regex_anchoring.py` (rojos antes, verdes con `fullmatch`).
**Cierre:** P5-07 (`fullmatch` en todos).
