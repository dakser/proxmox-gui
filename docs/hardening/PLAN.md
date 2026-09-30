# Plan de endurecimiento — proxmox-gui

Objetivo: dejar el proyecto instalable desde **tu propio repositorio de GitHub**, con los hallazgos de
`FINDINGS.md` cerrados o documentados como riesgo residual, y con evidencia de que cada cambio funciona.
Reglas de operación (ramas, commits, condiciones de parada, informe final): `AUTONOMY.md`.

Alcance: **instalaciones nuevas**. No se soporta migrar una instalación existente del upstream; el diseño
puede cambiar rutas, usuarios y unidades sin compatibilidad hacia atrás.

## Cómo leer este plan

- Cada tarea tiene ID (`P1-03`), hallazgos que cierra, qué hacer, archivos y **Aceptación**: comandos cuyo
  resultado debe verificarse antes de marcar la tarea. Sin aceptación verde, la tarea no se marca.
- Marca `[x]` al terminar, en este archivo, dentro del mismo commit de la tarea.
- Si una tarea exige algo que solo un humano puede hacer (clave de firma, ajustes de GitHub, laboratorio PVE),
  prepara todo lo demás y anótalo en `HUMAN-TODO.md`. No te detengas.
- Orden recomendado: P0 → P7. Las fases P1 a P6 tocan archivos distintos en su mayoría, pero comparten
  `bootstrap.sh`/`install.sh`; trabájalas en secuencia para evitar conflictos.

## Registro de decisiones (valores por defecto)

Estas decisiones se toman ya para que la ejecución sea autónoma. Cada una se copia a `DECISIONS.md` con la marca
`[REVISAR]` para que las confirmes o cambies al final.

| ID | Decisión | Valor por defecto | Alternativa |
|----|----------|-------------------|-------------|
| D1 | Canal SSH LXC→nodo (community-scripts) | Opt-in, apagado por defecto, con gate restringido | Eliminar la función; usar solo API tokens |
| D2 | Autenticidad de releases | Firma con `ssh-keygen -Y sign` (clave ed25519 del dueño del fork), verificación con `ssh-keygen -Y verify` | GPG; cosign (requiere binario extra en el LXC) |
| D3 | Dónde se firma | Localmente, con `scripts/release-sign.sh`; CI solo construye y sube artefactos sin firmar | Clave de firma como secreto de Actions |
| D4 | Frontend | Se compila en CI, se retira `frontend/build/` del árbol, viaja dentro del tarball de release | Mantener `build/` versionado |
| D5 | Runtime | Node 22 LTS y Python 3.12 descargados con URL y SHA-256 fijados en `deploy/pins.env` | Cambiar la plantilla a Debian 13 |
| D6 | Usuarios en el LXC | `proxmox-gui` (API + worker) y `proxmox-gui-web` (Node), sin acceso del segundo a `/etc/proxmox-gui` ni a Redis | Un solo usuario |
| D7 | Redis | Socket Unix con permisos de grupo si arq lo admite; si no, loopback + `requirepass` desde archivo. Serializador JSON en ambos casos | Dejar como está |
| D8 | Contenedores permitidos al gate | Solo LXC sin privilegios con tag `proxmox-gui` | Permitir privilegiados con un flag explícito |
| D9 | Ruta de actualización | Servicio root de systemd disparado por unidad `.path`; el worker solo escribe una solicitud | Ejecutar update.sh por sudo (descartado) |
| D10 | Ingress | Caddy con `tls internal`; el sitio se renderiza al arrancar con la IP actual o usa `--fqdn` | Certificado ACME público con `--public-hostname` |
| D11 | Docs de API | `/api/docs`, `/api/redoc`, `/api/openapi.json` desactivados salvo `PROXMOX_GUI_ENABLE_DOCS=true` | Dejar públicos |
| D12 | `.planning/` | No se toca; se escanea y se reporta | Eliminar del fork |

## P0 — Preparación, línea base y red de seguridad

Meta: saber qué funciona **antes** de cambiar nada, y poder verificar los scripts de root sin un PVE real.

- [x] **P0-01 Entorno.** Confirmar remoto `origin` = tu fork y crear rama `hardening/main`. Preparar Python 3.12
  (`uv python install 3.12`, o `docker run python:3.12` si `uv` no llega a GitHub), Node 22 y `pnpm`.
  Instalar herramientas: `ruff`, `mypy`, `pytest`, `bandit`, `pip-audit`, `shellcheck` (`pip install shellcheck-py`
  si no hay binario), `gitleaks` (binario o `pip install detect-secrets` como alternativa).
  *Aceptación:* `python3.12 --version`, `node --version`, `shellcheck --version` responden. Anotar versiones en `LOG.md`.
- [x] **P0-02 Línea base de tests.** Ejecutar `cd backend && pytest -q` y `cd frontend && pnpm install --frozen-lockfile &&
  pnpm test` (y `pnpm lint`/`pnpm check` si existen en `package.json`). Guardar el resultado en `docs/hardening/BASELINE.md`
  (pasan/fallan, tiempo, comando exacto). Los tests que ya fallan en la línea base se listan aparte y **no** se cuentan
  como regresión.
  *Aceptación:* `BASELINE.md` existe con cifras reales.
- [x] **P0-03 Escaneos iniciales.** `gitleaks` sobre todo el historial y `.planning/`; `bandit -r backend/app`;
  `pip-audit -r` sobre las dependencias fijadas; `pnpm audit --prod`; búsqueda de hostnames/IPs/dominios reales en
  `.planning/` y `docs/`. Resultados en `docs/hardening/SCANS-BASELINE.md`. No se borra nada (D12); se reporta.
  *Aceptación:* archivo generado; cada hallazgo con severidad y decisión (corregir/aceptar/reportar).
- [x] **P0-04 Script único de verificación.** Crear `scripts/check.sh` que ejecute, en este orden y saliendo al primer
  fallo: `shellcheck` sobre `deploy/**/*.sh` y `scripts/*.sh`; `bash -n` de cada script; `ruff check` y `mypy`
  (según `backend/ruff.toml` y `mypy.ini`); `pytest -q`; `pnpm lint && pnpm test`; y las pruebas de scripts (P0-05).
  *Aceptación:* `scripts/check.sh` termina en 0 sobre el estado actual, o falla solo por lo registrado en `BASELINE.md`.
- [x] **P0-05 Arnés para scripts de root.** Crear `deploy/tests/` con: `lib.sh` (aserciones mínimas, sin dependencias);
  `shims/` con ejecutables falsos `pct`, `pvesh`, `pveam`, `systemctl`, `runuser`, `visudo`, `ssh-keygen` (real,
  no shim), `curl`, `apt-get` que registran sus argumentos en un log y devuelven salidas configurables;
  y `run.sh` que ejecuta cada `test_*.sh` con `PATH=shims:$PATH` y directorios temporales en lugar de `/etc`, `/opt`,
  `/var/lib` (los scripts deben aceptar `PGUI_ROOT` como prefijo de pruebas; añadirlo en P2/P3).
  Añadir un primer test que reproduzca hoy los bugs de `install.sh` (HOSTNAME heredado, flag sin valor, CTID ajeno).
  *Aceptación:* `deploy/tests/run.sh` corre y **falla** en esos casos (prueba roja documentada); se vuelve verde en P2.
- [x] **P0-06 CI mínimo.** `.github/workflows/ci.yml` con jobs `shell` (shellcheck + deploy/tests), `backend`
  (Python 3.12: ruff, mypy, pytest), `frontend` (Node 22, pnpm frozen-lockfile, lint, test, build) y `scans`
  (gitleaks, bandit, pip-audit, pnpm audit). Acciones fijadas por SHA de commit, `permissions: contents: read`.
  Añadir `.github/dependabot.yml` (pip, npm, github-actions, semanal).
  *Aceptación:* `actionlint` (o `python -c "import yaml"`) valida el YAML; los comandos de cada job corren en local.

Cierre de fase: commit `chore(hardening): baseline, harness and CI`. Actualizar `LOG.md`.

## P1 — Frontera SSH LXC → nodo PVE (F-01, F-11)

Meta: que una app comprometida no pueda usar el canal SSH más allá de `pct exec` en contenedores propios.

- [x] **P1-01 Especificación.** Escribir `docs/hardening/SSH-GATE.md` con el protocolo entre cliente y gate.
  Requisitos: la entrada de `authorized_keys` fuerza `command="/usr/local/sbin/proxmox-gui-ssh-gate"`; el cliente
  envía `SSH_ORIGINAL_COMMAND` = `preflight` o `exec <vmid>`; para `exec`, la primera línea de stdin es un JSON
  `{"env": {...}, "argv": [...]}` y el resto es el stdin del proceso (respuestas de whiptail). Sin `sh -c` del
  lado del gate: se invoca `pct exec <vmid> -- ...` con lista de argumentos.
  *Aceptación:* documento revisado contra cómo lo usa `provisioning_functions.py` (líneas ~360-380: qué `env`,
  `argv` y `stdin_data` se envían hoy) y contra `build.func` de community-scripts; la spec conserva ese comportamiento.
- [x] **P1-02 Gate del lado del host.** Implementar `deploy/host/proxmox-gui-ssh-gate` en Perl con `JSON::PP` (Perl está
  garantizado en PVE) o Bash si el protocolo lo permite sin parsear JSON. Reglas: `vmid` con `^[1-9][0-9]{2,8}$`;
  el CT debe existir en este nodo, tener `unprivileged: 1` y el tag `proxmox-gui` (D8); nombres de variables de
  entorno `^[A-Z_][A-Z0-9_]*$` y se rechazan `LD_*`, `BASH_ENV`, `ENV`, `PATH`, `IFS`; tamaño máximo de JSON y de
  argv; tiempo máximo; cada llamada se registra con `logger -t proxmox-gui-gate` (vmid, hash del argv, exit code).
  `preflight` ejecuta únicamente `pct list`.
  *Aceptación:* tests en `deploy/tests/test_ssh_gate.sh` (con `pct` falso) cubren: vmid inválido, CT inexistente,
  CT privilegiado, CT sin tag, env prohibido, argv con metacaracteres (llegan literales), JSON malformado, exceso de
  tamaño. `shellcheck` (o `perl -c`) limpio.
- [x] **P1-03 Instalación opt-in en `install.sh`.** Flag `--enable-community-scripts` (apagado por defecto). Solo entonces:
  instalar el gate en el host, leer la clave pública del CT, **validarla** (una sola línea, `ssh-ed25519`, sin opciones,
  `ssh-keygen -l -f` correcto), y escribir la entrada `restrict,from="<IP del CT>",command="..." <clave> proxmox-gui@<ctid>`
  en `/root/.ssh/authorized_keys` de forma idempotente (reemplazar la línea anterior con el mismo comentario,
  no duplicar). Añadir `--ip`/`--gw` para IP estática; sin ellos, advertir que `from=` depende de la IP actual del CT.
  Capturar la host key del nodo (`/etc/ssh/ssh_host_ed25519_key.pub`) y entregarla al CT para el `known_hosts` (P1-05).
  *Aceptación:* test con `pct`/`ssh-keygen` falsos: clave multilínea o con `command=` se rechaza; doble ejecución deja una
  sola línea; sin el flag no se toca `authorized_keys`.
- [x] **P1-04 Desinstalación y revocación.** `install.sh --uninstall --ctid N`: quita del host la entrada
  `authorized_keys` de ese CT (por comentario exacto), retira el gate si no quedan otras entradas, y solo con `--purge`
  destruye el CT (pide confirmación escrita del CTID).
  *Aceptación:* test de ida y vuelta (install → uninstall) deja `authorized_keys` idéntico al inicial.
- [x] **P1-05 Cliente SSH en el backend.** En `connector.py::_ssh_pct_exec` y `networks/preflight.py::_run_ssh_probe`:
  pasar `-i /etc/proxmox-gui/gui_ed25519`, `-o IdentitiesOnly=yes`, `-o UserKnownHostsFile=/var/lib/proxmox-gui/ssh/known_hosts`,
  `-o StrictHostKeyChecking=yes`, `-o BatchMode=yes`, `-o ClearAllForwardings=yes`, `-T`; construir el argv con el
  protocolo de P1-01 (sin `shlex.quote` de una cadena de shell remota). Validar `node` contra
  `^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$`. Rutas configurables por `Settings`.
  *Aceptación:* tests unitarios actualizados en `test_connector.py` y `test_ssh_preflight.py` comprueban el argv exacto;
  un `node` con espacios, `-o...` o `;` se rechaza antes de lanzar el proceso.
- [x] **P1-06 Tag en el aprovisionamiento.** Asegurar que los LXC creados por el flujo community-scripts salen con el tag
  `proxmox-gui` (y sin privilegios) para pasar el gate. Localizar el punto de creación en `provisioning_functions.py`.
  *Aceptación:* test en `test_provisioning.py` que verifica el tag en la llamada a PVE.
- [x] **P1-07 Estado deshabilitado.** Si el canal no está habilitado, el preflight devuelve `{ok: false, detail}` con un
  mensaje claro y el wizard deshabilita solo la ruta community-scripts (VM y LXC simples siguen funcionando).
  *Aceptación:* test del preflight; revisión del componente del wizard que lo consume (`frontend/src`).

Cierre: commit(s) `feat(security): restricted SSH gate for community-scripts (opt-in)`.

- [x] **P1-08 Gate: residuos (F-16).** Escalar `TERM`→`KILL` tras gracia; escapar/limitar todo dato externo en el log; documentar en `SSH-GATE.md` el riesgo residual de la tag; test para cada punto.

## P2 — Puerta de entrada: `install.sh` (F-04 parcial, F-07, F-08)

- [x] **P2-01 Validación de entradas.** Parser que exige valor para cada flag; `CTID`, `CPU`, `RAM_MB`, `DISK_GB` numéricos y
  en rango; `STORAGE`, `BRIDGE`, hostname y `--release` con listas de caracteres permitidos (`^[A-Za-z0-9][A-Za-z0-9._-]*$`,
  sin `..`); `REPO_URL` solo `https://github.com/<owner>/<repo>`. Renombrar `HOSTNAME` a `CT_HOSTNAME` (default `proxmox-gui`).
  Corregir el texto de ayuda a la rama real. Dejar de aceptar `CTID` y `HOSTNAME` por entorno; usar prefijo `PGUI_`.
  Aceptar `PGUI_ROOT` (prefijo de pruebas) para el arnés.
  *Aceptación:* `deploy/tests/test_install_args.sh` verde (los casos rojos de P0-05).
- [x] **P2-02 Marcador de contenedor propio.** Al crear el CT, añadir `--tags proxmox-gui` y `--description` con un identificador.
  La ruta de actualización solo actúa si el CT existe, tiene ese tag y `/etc/proxmox-gui/.installed` dentro del CT.
  *Aceptación:* test: CTID ajeno → aborta sin ejecutar nada dentro del CT.
- [x] **P2-03 Descarga verificable en el host.** Sustituir `curl | bash` dentro del LXC: el host descarga a un directorio
  temporal el tarball de la release, `SHA256SUMS` y `SHA256SUMS.sig`, verifica la firma con `ssh-keygen -Y verify`
  contra `deploy/release-signers` (allowed_signers, clave pública tuya), verifica el hash, y entrega al CT con `pct push`.
  Dentro del CT se ejecuta `bootstrap.sh` desde el tarball ya verificado. Un solo origen: eliminar el `git clone` del
  bootstrap. `--release` obligatorio (tag `vX.Y.Z`); `latest` y ramas no se admiten.
  *Aceptación:* test con release falsa: firma inválida, hash alterado y tag inexistente abortan antes del primer `pct push`.
- [x] **P2-04 Errores visibles.** Quitar los `|| true` y `2>/dev/null` que ocultan fallos (plantilla, `pveam`, lectura de
  clave). Fallback de plantilla solo si `pveam available` no responde, con aviso claro. `trap` con limpieza del directorio temporal.
- [x] **P2-05 Banner y token de setup.** Imprimir la URL y el comando para leer el token de setup (P3-07). No imprimir secretos
  en logs.

## P3 — Modelo de archivos y privilegios dentro del LXC (F-02, F-06, F-09, F-10, F-12)

- [x] **P3-01 Código inmutable.** `/opt/proxmox-gui/releases/<tag>` y todo su contenido `root:root`, modo 755/644; el usuario de
  servicio solo lee y ejecuta. Instalar el backend **no editable** (sin `-e`); `--no-deps` para el paquete propio.
  *Aceptación:* test en arnés: tras el bootstrap simulado, `find releases -not -user root` vacío; ningún archivo escribible
  por grupo/otros.
- [x] **P3-02 Datos y secretos.** `/var/lib/proxmox-gui` (`proxmox-gui`, 0750). `/etc/proxmox-gui` `root:proxmox-gui` 0750 y
  archivos secretos `root:proxmox-gui` 0440 (la app los lee, no los reemplaza). Ajustar `gen-master-key.sh` y
  `gen-jwt-secret.sh` (crear con `umask 077`, `install -m` atómico, propietario root) y verificar que el chequeo de permisos de
  `app/core/cipher.py` (`st_mode & 0o077`) sigue satisfecho o actualizarlo con test.
  *Aceptación:* `pytest backend/tests/test_cipher.py` verde; test del arnés sobre modos y propietarios.
- [x] **P3-03 Usuario separado para el frontend.** Crear `proxmox-gui-web` sin shell ni acceso a `/etc/proxmox-gui`, Redis ni
  `/var/lib/proxmox-gui`; actualizar `proxmox-gui-frontend.service`. Directorios accesibles por camino, no por grupo compartido.
- [x] **P3-04 Sin sudo.** Eliminar el sudoers y toda referencia a `sudo -n systemctl` (se reemplaza en P4). Quitar la dependencia de
  `visudo`.
- [x] **P3-05 Toolchain fijado.** Crear `deploy/pins.env` con URL + SHA-256 de: tarball de Node 22 LTS (linux-x64) y Python 3.12
  standalone (o `uv` con su hash). `bootstrap.sh` descarga a archivo, verifica con `sha256sum -c` y solo entonces instala; se
  eliminan `curl | sh`, `nodejs npm` de apt y `pip install --upgrade pip setuptools wheel`. Si no hay red para calcular los hashes,
  dejar `TODO-PIN` que **hace fallar** `scripts/check.sh` y crear `scripts/update-pins.sh` para calcularlos; anotar en `HUMAN-TODO.md`.
- [x] **P3-06 Dependencias con hashes.** Generar `backend/requirements.lock` con `uv pip compile --generate-hashes` (fuente:
  `pyproject.toml`), instalar con `pip install --require-hashes --only-binary=:all: -r requirements.lock`. CI verifica que el
  lockfile no está desactualizado.
- [x] **P3-07 Token de setup.** El bootstrap genera `/etc/proxmox-gui/setup-token` (`root:proxmox-gui` 0440, 32 bytes aleatorios en
  urlsafe). Implementación del lado app en P5-01.
- [x] **P3-08 Redis.** Socket Unix (`port 0`, `unixsocket`, `unixsocketperm 660`, usuario `proxmox-gui` en el grupo `redis`) si arq lo
  soporta; si no, `requirepass` desde archivo 0440. Verificar en el código de arq 0.26.3 qué opciones admite `RedisSettings`
  antes de decidir. Configuración persistente en un drop-in propio, no editando `/etc/redis/redis.conf` con `echo >>`.
- [x] **P3-09 Unidades systemd endurecidas.** Para API/worker: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/proxmox-gui`,
  `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `NoNewPrivileges`, `CapabilityBoundingSet=` (vacío), `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
  `RestrictNamespaces`, `LockPersonality`, `ProtectKernelTunables/Modules/Logs`, `ProtectControlGroups`, `ProtectClock`,
  `SystemCallFilter=@system-service`, `SystemCallArchitectures=native`, `UMask=0077`, `MemoryDenyWriteExecute=yes` (probar con el
  API; si rompe, documentar y quitar). Frontend: igual salvo `MemoryDenyWriteExecute`, sin `AF_UNIX` innecesario, sin escritura.
  Ejecutar `systemd-analyze security <unidad>` en el contenedor de pruebas (si hay docker con systemd) y registrar la puntuación.
  *Aceptación:* servicios arrancan (P7-02); puntuación anotada en `LOG.md`.
- [x] **P3-10 Caddy.** Renderizar el Caddyfile al arrancar con la IP vigente (unidad oneshot `proxmox-gui-caddy-render.service` antes de
  `caddy`), o usar `--fqdn`. Cabeceras: mantener las existentes; retirar `'unsafe-inline'` de `script-src` si SvelteKit lo permite con
  hash/nonce (`kit.csp` en `svelte.config.js`), si no, documentar por qué. `request_body { max_size }` razonable, límites de
  timeout, y bloquear `/api/docs` y `/api/openapi.json` si D11 no lo cubre en la app.
  *Aceptación:* `caddy validate --config` sobre la plantilla renderizada (si hay binario) o test del render; cambio de IP simulado regenera.

## P4 — Actualizaciones seguras (F-03, F-05, F-04)

- [x] **P4-01 Especificación.** `docs/hardening/UPDATER.md`: flujo, estados, formato de `status.json`, política de versiones
  (monótona; solo `--allow-downgrade` desde consola root), retención (actual + 2 anteriores), qué valida cada paso.
- [x] **P4-02 Updater raíz.** `deploy/host-lxc/proxmox-gui-updater` (instalado en `/usr/local/sbin`, `root:root` 0755). Subcomandos
  `apply`, `rollback`, `status`. Pasos: leer la solicitud (≤64 bytes, regex `^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$`, borrarla
  primero); obtener `SHA256SUMS`, `.sig` y tarball de la URL fijada en `/etc/proxmox-gui/release.conf` (propiedad de root);
  verificar firma y hash; extraer en un directorio temporal de root con `--no-same-owner --no-same-permissions`, rechazando
  rutas absolutas, `..`, enlaces fuera del árbol, hardlinks y dispositivos (revisar con `tar -tvf` antes de extraer); comprobar
  estructura y `frontend/build/index.js`; **nunca** tocar el release activo; instalar dependencias con hashes en un venv nuevo;
  backup `sqlite3 .backup` a `/var/lib/proxmox-gui/backups/`; migrar con `runuser -u proxmox-gui` (sin privilegios) usando el
  venv nuevo; cambiar el symlink de forma atómica; reiniciar API y esperar `/api/v1/health` (60 s); reiniciar frontend y worker;
  si falla algo: volver al symlink previo, restaurar la BD solo si la migración llegó a correr, reiniciar, registrar `failed`.
  Escribir `status.json` (root, 0644) en cada transición. Limpiar releases antiguas.
  *Aceptación:* `deploy/tests/test_updater.sh` con `curl`/`systemctl`/`runuser` falsos y un tarball de prueba firmado con una
  clave de prueba: éxito; firma inválida; hash inválido; tag hostil (`.`, `..`, `a b`, `$(id)`); tarball con `../` y con symlink
  externo; release igual al activo; downgrade; falla de migración → rollback con BD restaurada; falla de health → rollback.
- [x] **P4-03 Unidades.** `proxmox-gui-updater.path` (vigila `/var/lib/proxmox-gui/update/request`) y `proxmox-gui-updater.service`
  (`Type=oneshot`, root, con `ProtectSystem=strict` y `ReadWritePaths` mínimos, `PrivateTmp`, `NoNewPrivileges`). `release.conf` fijado
  por el bootstrap desde el repo/tag con el que se instaló (D2/F-04: nunca el upstream por defecto).
- [x] **P4-04 Worker sin privilegios.** Reescribir `run_self_update` en `selfupdate_functions.py`: valida la versión pedida, escribe la
  solicitud, sondea `status.json` y refleja los estados en la fila del job. Sin `subprocess`, sin `sudo`, sin extraer nada.
  Adaptar `app/selfupdate/service.py` (manifest desde tu repo, configurable) y `test_selfupdate.py`.
  *Aceptación:* `pytest backend/tests/test_selfupdate.py` verde con casos nuevos: solicitud inválida, updater ausente, estado `failed`.
- [x] **P4-05 Ruta CLI.** `install.sh --update` deja de tener lógica propia: verifica la release en el host y escribe la solicitud /
  invoca el updater dentro del CT. Eliminar `deploy/lxc/update.sh` (o dejarlo como wrapper de una línea hacia el updater).

- [x] **P4-06 Updater seguro frente a la app (F-15, F-17).** Mover backups/staging a `/var/lib/proxmox-gui-updater` (`root:root` 0700); leer `update/request` con un único fd sin seguir enlaces; no usar rutas de la app para escritura de root salvo creando ficheros con `install`/`mktemp` dentro de un directorio root y `rename` atómico; sanear y acotar `status.json` y mensajes; extraer el tarball con el mismo intérprete que lo valida y rechazar miembros que no sean fichero/directorio.
  *Aceptación:* tests del arnés con symlinks plantados en `update/`, `backups/`, `request` y `${DB_FILE}.restore` que demuestren que root no toca el destino del enlace; tarball con hardlink/duplicados/`..` rechazado.

## P5 — Endurecimiento de la aplicación (F-06, F-09, F-11, F-12, F-13)

- [x] **P5-01 Token de setup en la API.** `POST /api/v1/setup/admin` exige `X-Setup-Token`; comparación con `hmac.compare_digest`;
  límite de tasa; sin fuga de información en errores; `GET /setup/status` indica `token_required` pero nada más. El wizard del
  frontend añade el paso del token. Tests en `test_setup.py`: sin token, token erróneo, token correcto, tras crear admin → 409.
- [x] **P5-02 Redis/arq.** Configurar `RedisSettings` según P3-08 y fijar `job_serializer`/`job_deserializer` JSON tanto al encolar
  (`main.py`) como en `WorkerSettings`. Comprobar que ningún argumento encolado necesita pickle (fechas, bytes, modelos) y adaptarlo.
  *Aceptación:* `test_jobs_infrastructure.py` verde + test de ida y vuelta JSON.
- [x] **P5-03 Origin en WebSockets.** Validar `Origin` contra una lista (`Settings.allowed_origins`, por defecto el host de la petición)
  en `/api/v1/ws/jobs` y `/api/v1/ws/console/...`; cerrar con 1008 si no coincide. Tests: sin Origin, Origin ajeno, Origin correcto.
- [x] **P5-04 Superficie HTTP.** Desactivar `/api/docs`, `/api/redoc`, `/api/openapi.json` salvo `PROXMOX_GUI_ENABLE_DOCS=true` (D11);
  activar `TrustedHostMiddleware` con hosts configurables (IP/FQDN del LXC); revisar cabeceras y que `X-Forwarded-*` solo se acepte desde
  el proxy local (ya existe en `core/source_ip.py`; añadir test si falta).
- [x] **P5-05 TLS de consola.** Confirmar en `console/proxy.py` si con `verify_ssl=False` se aplica el fingerprint guardado; si no, aplicarlo
  (comparación del certificado del peer contra `tls_fingerprint`) con tests en `test_console.py` y `test_tls_pinning.py`.
- [x] **P5-06 Registro de clústeres.** Revisar `clusters/routes.py` y `service.py`: SSRF (rechazar esquemas/puertos inesperados y, según política,
  direcciones de loopback/metadata), límites de longitud, no reflejar cuerpos de error de PVE sin sanear. Tests.
- [x] **P5-07 Community-scripts.** Test que demuestre que un slug hostil (`; id`, `$(id)`, `../`, URL completa) no llega al shell y que la URL
  queda anclada al commit del catálogo (`provisioning_functions.py`, `catalog/service.py`).
- [x] **P5-08 Auditoría rápida del resto.** Revisar de forma dirigida (grep + lectura) `auth/`, `pats/`, `mcp/server.py`, `users/`, `teams/`,
  `quotas/`, `inventory/`, `lifecycle/` en busca de: rutas sin dependencia de autenticación, comprobaciones de `tenant_id`/team ausentes
  (IDOR), uso de `text()`/SQL crudo, `eval`/`exec`, deserialización insegura, secretos en logs. Todo hallazgo → test que falla → arreglo.
  Resultado en `docs/hardening/APP-REVIEW.md`, incluso si no hay hallazgos.

## P6 — Cadena de suministro y pipeline de release (F-04, F-10)

- [x] **P6-01 Frontend fuera del árbol.** `git rm -r --cached frontend/build`, añadir a `.gitignore`, ajustar `README`/docs. El build se hace en CI.
  (El historial conserva el binario; documentarlo. No reescribir historia sin que el dueño lo pida.)
- [x] **P6-02 Workflow de release.** `.github/workflows/release.yml` al empujar un tag `v*`: instalar Node 22 + pnpm (`--frozen-lockfile`),
  compilar, ensamblar el tarball determinista (`--sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner`) con `backend/`, `frontend/build`,
  `deploy/`, `requirements.lock`, `deploy/pins.env`, generar `SHA256SUMS` y SBOM (CycloneDX), y subir todo a un release en borrador.
  Permisos mínimos y acciones fijadas por SHA.
- [x] **P6-03 Firma local.** `scripts/release-sign.sh <tag>`: descarga `SHA256SUMS` del borrador, firma con `ssh-keygen -Y sign -n proxmox-gui-release`,
  verifica localmente y sube `SHA256SUMS.sig`. `deploy/release-signers` con **marcador** `REEMPLAZAR-CON-TU-CLAVE-PUBLICA` que hace fallar el
  instalador y `scripts/check.sh` hasta que el dueño lo sustituya (HUMAN-TODO).
- [x] **P6-04 Parametrizar el fork.** `REPO_URL` por defecto = el `origin` del fork (script `scripts/set-fork.sh <owner/repo>` reescribe los valores
  por defecto en `install.sh`, `selfupdate/service.py`, `release.conf`, README). Ninguna referencia a `chloepriceless/*` debe quedar como origen de código
  ejecutable; sí en `LICENSE`/créditos.
  *Aceptación:* `grep -rn "chloepriceless" --include=*.sh --include=*.py --include=*.yml --include=*.conf .` no devuelve nada ejecutable.
- [x] **P6-06 Escaneos bloqueantes.** Quitar `continue-on-error` de bandit, pip-audit y `pnpm audit` en `ci.yml` (pip-audit ya en 0); registrar excepciones puntuales en `SCANS-BASELINE.md`.
- [x] **P6-05 Documentación de instalación segura.** `deploy/README.md` y `README.md`: flujo recomendado (descargar `install.sh` a un archivo, comparar su
  SHA-256 publicado en la release, leer, ejecutar con `--release vX.Y.Z`), modelo de amenazas resumido, qué implica habilitar community-scripts,
  procedimiento de rotación de `master.key`/JWT, y desinstalación.

## P7 — Verificación integral e informe

- [ ] **P7-01 Suite completa.** `scripts/check.sh` en verde; comparar con `BASELINE.md`; cero regresiones. Cobertura de los módulos tocados no menor que en la línea base.
- [ ] **P7-02 Prueba de humo del instalador con systemd real.** *(revisada)* Además del script local `deploy/tests/smoke-systemd.sh`, añadir a `ci.yml` un job `smoke-systemd` en runners de GitHub (ubuntu, Docker con daemon disponible allí; imagen Debian 12 con systemd) que ejecute la prueba de abajo y suba logs como artefacto. Es el nivel "entorno real sin PVE".
  Detalle original: Si hay `docker` con daemon disponible: imagen Debian 12 con systemd
  (`--privileged --cgroupns=host`), ejecutar `bootstrap.sh` con un tarball de release local de prueba, comprobar que arrancan `caddy`, `redis`,
  API, worker y frontend, que `GET https://127.0.0.1/api/v1/health` responde, que `/setup` exige token y que `systemd-analyze security` no empeora.
  Si no hay docker, dejar el script `deploy/tests/smoke-systemd.sh` listo y anotarlo en `HUMAN-TODO.md`. Esta prueba no sustituye a un PVE real.
- [ ] **P7-03 Pruebas negativas de seguridad.** Un test por hallazgo F-01…F-13 que falle contra el commit base y pase ahora (o justificar por qué no es
  testeable automáticamente). Tabla hallazgo → test → commit en el informe.
- [ ] **P7-04 Escaneos finales.** Repetir P0-03; comparar con la línea base; ningún hallazgo nuevo de severidad ≥ media sin decisión registrada.
- [ ] **P7-05 Lista de validación en laboratorio.** `docs/hardening/LAB-CHECKLIST.md`: pasos para un PVE anidado o nodo de pruebas (instalar sin y con
  `--enable-community-scripts`, crear el primer admin con token, registrar un clúster, VM/LXC, consola, backup/restore, release N → N+1 y
  rollback forzado, desinstalar y comprobar `authorized_keys`), con el resultado esperado de cada paso.
- [ ] **P7-06 Informe final.** `docs/hardening/HARDENING-REPORT.md` con la plantilla de `AUTONOMY.md`.
