# Revisión independiente de seguridad — proxmox-gui (fork dakser), tag v0.1.0

> Nota: este documento se recuperó del texto pegado de `/tmp/review-final.md` (la copia original estaba solo en el servidor de la revisión). Su contenido es el del revisor; solo se añade esta nota. La limitación del punto N-2 (`SHA256SUMS.sig` ausente) se debía a que el revisor leyó una copia descargada antes de firmar: la firma ya estaba subida a la release `v0.1.0` y se verificó después desde un entorno limpio.


Fecha: 2026-10-01
Revisor: independiente, **solo lectura** (no se editó, firmó, fusionó ni se tocó ninguna clave; no se leyó `~/.ssh`).
Checkout revisado: `8dc17f4` (merge de `hardening/main`), que corresponde al árbol del tag `v0.1.0`.
Artefacto de release revisado: `/tmp/rel/` (tarball, install.sh, SHA256SUMS, SBOMs) y su extracción `/tmp/rel/x`.

## Método

- Leídos primero `FINDINGS.md`, `HARDENING-REPORT.md`, `DECISIONS.md`, `LAB-CHECKLIST.md`.
- Después, **sin fiarme del informe**, se leyó el código de cada frontera de privilegio y se contrastó cada
  hallazgo F-01…F-18 contra la implementación real y contra la existencia/forma de su test negativo.
- Lo marcado **[verificado leyendo]** se comprobó en el código. Lo marcado **[no ejecutado]** no se re-ejecutó
  en esta sesión (no corrí la suite completa ni un PVE); se infiere de la lectura y de la presencia del test.

Resumen del veredicto: **el endurecimiento es real y de alta calidad.** No encontré ningún hallazgo de
severidad Alta nuevo ni ningún F-01…F-18 que esté abierto o mal cerrado. Hay **un** hallazgo de severidad
Baja (contenido del artefacto de release) y varias observaciones menores. El fork está en condiciones de
firmarse y publicarse una vez completados los pasos humanos ya documentados (firma de `SHA256SUMS`).

---

## 1. Verificación de los hallazgos F-01…F-18

| ID | Sev | Estado real (verificado leyendo) | Evidencia en código |
|----|-----|----------------------------------|---------------------|
| F-01 | Alta | **Cerrado.** Canal SSH opt-in; gate con `command=`/`from=`/`restrict`; protocolo estructurado sin shell. | `deploy/host/proxmox-gui-ssh-gate` (exec por lista, `exec {…} @cmd`), `install.sh:397` (entrada restringida) |
| F-02 | Alta | **Cerrado.** Release `root:root`, `chmod -R u=rwX,go=rX`; pip no editable; unidades root. | `bootstrap.sh:155-167`, updater `457-458,473-478` |
| F-03 | Alta | **Cerrado.** El worker solo escribe una solicitud; updater root por `.path`, sin sudo. | `selfupdate/service.py:write_request`, `proxmox-gui-updater.path`, unidad worker sin sudoers |
| F-04 | Alta | **Cerrado.** Firma `ssh-keygen -Y verify` + hash en host y de nuevo en el updater; clave real embebida; `--release` obligatorio, sin ramas. | `install.sh:299-319`, updater `424-431`; clave `dakser` en `release-signers` == embebida en install.sh |
| F-05 | Alta | **Cerrado.** `TAG_RE` estricto, jamás sobrescribe activo/previo, backup+rollback con BD, extracción segura. | updater `384-388,438-448,481-534` |
| F-06 | Alta | **Cerrado.** `/setup/admin` exige `X-Setup-Token` (compare_digest), rate-limit, fail-closed, re-check `no_admin_yet` en la transacción. | `setup/routes.py`, `setup/service.py:create_initial_admin` |
| F-07 | Media | **Cerrado.** `need_value` exige valor; CPU/RAM/DISK/CTID/IP/tag validados por allow-list; `REPO_URL` anclado a github. | `install.sh:127-194` |
| F-08 | Media | **Cerrado.** CT propio = tag + marcador en descripción + `/etc/proxmox-gui/.installed`; CTID ajeno aborta. | `install.sh:215-225,440-441,476-478` |
| F-09 | Media | **Cerrado.** Usuario `proxmox-gui-web` separado (`InaccessiblePaths`), Redis socket unix sin TCP, serializador JSON. | unidades systemd; `redis-server.service.d/proxmox-gui.conf` |
| F-10 | Media | **Cerrado** (con matiz, ver §2). Toolchain con SHA-256, lock con hashes, build en CI, release firmada. | `bootstrap.sh:81-117`, `pins.env`, `requirements.lock` |
| F-11 | Media | **Cerrado.** Cliente SSH con `-i`, `IdentitiesOnly`, `UserKnownHostsFile`, `StrictHostKeyChecking=yes`, nodo validado. | `clusters/ssh_gate.py:build_ssh_argv`, `validate_node` |
| F-12 | Media | **Cerrado.** Unidades endurecidas; Caddy renderiza IP vigente; CSP sin `unsafe-inline` (per-request de SvelteKit); Origin WS validado. | unidades, `render-caddyfile.sh`, `jobs/ws.py:origin_allowed` (aplica a jobs **y** consola) |
| F-13 | Media | **Cerrado.** Pinning TLS en el relay de consola; política SSRF de registro de clúster; slug/sha community-scripts validados. | `console/proxy.py:_pinned_connection_class`, `clusters/target_policy.py`, `jobs/provisioning_functions.py:_validate_slug/_validate_commit_sha` |
| F-14 | Baja | **Cerrado.** Desinstalador con revocación idempotente; `.planning` no borrado pero escaneado. | `install.sh:do_uninstall` |
| F-15 | Alta | **Cerrado.** Área de trabajo del updater `root:root 0700` fuera de la app; `read_request`/`restore_db` por fd con `O_NOFOLLOW` y `rename` que reemplaza (no sigue) enlaces. | updater `PYHELPER` (`read_request`,`restore_db`), `WORK_DIR` |
| F-16 | Media | **Cerrado.** `TERM`→`KILL` con gracia; todo dato externo saneado antes de loggear (`clean`). | gate `alarm_step`, `clean`, `log_msg` |
| F-17 | Media | **Cerrado.** El **mismo** intérprete (Python `tarfile`) valida y extrae; rechaza symlink/hardlink/dispositivo/duplicado/`..`/setuid; `filter="data"`. | updater `check_members`/`extract` |
| F-18 | Media | **Cerrado.** Validadores migrados a `fullmatch` (slug, commit, tag, usuario cloud-init, etc.). | `provisioning_functions.py`, `selfupdate/service.py:TAG_RE.fullmatch`, `cloudinit.py:252` |

**El artefacto real coincide con el código:** el `install.sh` de `/tmp/rel/` es idéntico a `deploy/install.sh`
y su SHA-256 coincide con `SHA256SUMS`; el tarball coincide con `SHA256SUMS`; la clave de firma embebida en
install.sh coincide con `deploy/release-signers`.

---

## 2. Hallazgos nuevos

### N-1 (Baja) — La release empaqueta herramientas de build en `frontend/node_modules`
- **Archivos:** `scripts/build-release.sh:48`, `frontend/package.json`, tarball `frontend/node_modules/`.
- **Qué se verificó leyendo:** el tarball contiene `frontend/node_modules/` con **24.436** de sus 25.463
  miembros (~227 MB descomprimido) e incluye paquetes de build como `vite` (6.x) y `typescript` (5.x), que
  en `package.json` están en `devDependencies`. Llegan porque son cierre transitivo *de producción* de
  `@sveltejs/kit` (dependencia prod real, importada de verdad por `frontend/build/server`), así que
  `pnpm install --prod` los instala igualmente.
- **Impacto:** no es una vulnerabilidad directa — el frontend corre como `proxmox-gui-web` en un sandbox sin
  acceso a secretos ni a la red de gestión, y nada de ese código de build se ejecuta en runtime. Es (a)
  superficie y tamaño innecesarios en el LXC y (b) una **imprecisión del informe**: `HARDENING-REPORT.md §6/§7`
  da a entender un árbol de producción mínimo y que "los avisos de dependencias de desarrollo no viajan en la
  release". Verifiqué que los dev-only puros sí quedan fuera (`vitest`, `eslint`, `prettier`, `jsdom`,
  `happy-dom`, `@hey-api` **no** están en el tarball), con lo que la afirmación sobre `vitest/js-yaml` se
  sostiene; pero `vite`/`typescript`/`tailwindcss` sí viajan.
- **Escenario concreto:** un futuro CVE en `vite`/`typescript` aparecería instalado en todos los LXC aunque no
  se ejecute; `pnpm audit --prod` (gate de CI) sí lo detectaría por ser prod, pero el informe sugiere que no
  está presente en el artefacto.
- **Cómo corregir (opcional):** evaluar el empaquetado con bundler de `adapter-node` (esbuild) para prescindir
  por completo de `frontend/node_modules`, o mover a `devDependencies` lo que no sea estrictamente runtime y
  confirmar que `node build/index.js` arranca; como mínimo, ajustar el texto del informe para reflejar el
  contenido real del tarball. No bloquea la publicación.

### N-2 (Informativa) — `SHA256SUMS` del artefacto aún sin `.sig`
- `/tmp/rel/` no incluye `SHA256SUMS.sig`. Es **lo esperado** (la firma es el paso humano 4 de
  `HARDENING-REPORT §7`, con `scripts/release-sign.sh`). Solo se anota para que no se publique sin firmar: el
  instalador y el updater rechazan una release sin firma válida, así que una publicación sin `.sig` dejaría el
  producto **no instalable** (fail-closed correcto, no inseguro).

### N-3 (Informativa) — Residuos ya documentados que conviene reconfirmar
Ninguno es un defecto nuevo; están en `DECISIONS.md`/`§6` del informe y los confirmo como aceptables:
- `verify_ssl=False` **sin** fingerprint guardado ⇒ TLS sin verificar en REST y en el relay de consola (TOFU
  ausente). Es la postura de laboratorio documentada; con fingerprint se hace pinning. `console/proxy.py` lo
  aplica correctamente **solo** cuando hay fingerprint y `verify_ssl=False`.
- Rebinding DNS entre `check_target` y la conexión real: mitigado por el pinning de certificado, no cerrable
  del todo. Correcto y documentado.
- La tag `proxmox-gui` del CT la puede poner cualquier admin PVE (puerta del gate): riesgo residual documentado.

---

## 3. Lo que revisé con especial cuidado y encontré correcto

- **Gate SSH (`proxmox-gui-ssh-gate`):** sin `sh -c`; `exec` por lista; `vmid` `\A[1-9][0-9]{2,8}\z`; exige
  CT existente, `unprivileged:1` y tag; denylist de env (`LD_*`,`BASH_*`,`PERL*`,`PYTHON*`,`PATH`,`IFS`,…);
  `argv[0]` no puede empezar por `-` ni contener `=` (no se cuela como flag de `env`); saneo del log;
  `TERM`→`KILL`. Robusto.
- **Updater root:** todo el acceso a rutas de la app y al tarball pasa por **un único** helper Python fd-based;
  trabaja en `/var/lib/proxmox-gui-updater` (root 0700); nunca toca el release activo; rollback de symlink y de
  BD; `flock`; descargas con `--proto =https --tlsv1.2 --max-filesize`; `REPO_URL` anclado a github y sin
  `.git`/`..`. Sólido frente a F-15/F-17.
- **install.sh:** verificación de firma+hash **antes** de crear nada; re-verificación dentro del CT;
  `--no-same-owner --no-same-permissions`; propiedad del CT por tag+marcador+`.installed`; `--purge` pide teclear
  el CTID.
- **Multi-tenancy / IDOR:** `inventory/access.py:resolve_resource` resuelve vía token privsep del equipo y
  **exige coincidencia de pool** (defensa en profundidad), el admin también opera a través de un token de equipo,
  y no filtra en Python ni filtra existencia (403 uniforme). Coherente con los invariantes del proyecto.
- **WebSockets:** `origin_allowed` (fail-closed ante Origin ausente; no vulnerable a substring como
  `evil.example/testserver`) se aplica a `/ws/jobs` y a `/ws/console/...`; auth y ownership **antes** de
  `accept()`.
- **Caddy:** `X-Forwarded-For` se **fija** al peer real en cada upstream (un XFF del cliente nunca llega a la
  lógica de IP de la API, que solo confía desde loopback); CSP de `/api/*` bloqueada; `admin off`.

## 4. Pruebas negativas
Confirmé que existen y tienen forma de prueba real (rojo-sin-arreglo) los tests citados: `test_ws_origin.py`
(incluye orígenes `evil.example` y trampas de substring), `test_updater.sh` (20 asertos de symlink/hardlink/
O_NOFOLLOW/.restore), `test_setup.py`, `test_console_pinning.py`, `test_cluster_target_policy.py`,
`test_authz_review.py`, `test_regex_anchoring.py`, `test_ssh_gate.sh`. **No re-ejecuté la suite completa en esta
sesión** [no ejecutado]; la ejecución verde es responsabilidad del CI y del paso de laboratorio.

## 5. Recomendación
Apto para firmar y publicar `v0.1.0` tras los pasos humanos ya listados (clave de firma → `SHA256SUMS.sig`,
ajustes de GitHub, validación en `LAB-CHECKLIST.md`). Considerar N-1 como mejora de higiene/precisión del informe,
no como bloqueante.
