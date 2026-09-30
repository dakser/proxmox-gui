# Informe de endurecimiento — 2026-09-30
Base: e485bc4 → Final: 2fa6417 (CI verde; el commit del propio informe va encima)   Rama: hardening/main (fork `dakser/proxmox-gui`)   Commits: 46

## 1. Resumen
- Cerrados los hallazgos F-01…F-18 y A1–A5 de la revisión de la app, cada uno con una prueba que falla contra la base y pasa ahora (o mutación, ver `FINDINGS-TESTS.md`).
- El instalador ya no se ejecuta por `curl | bash`: descarga → verifica firma ssh-keygen y hash **en el host** → solo entonces crea el LXC. El canal SSH LXC→nodo (community-scripts) es opt-in y va por una puerta restringida (`command=`, `from=`, solo LXC sin privilegios con tag).
- El self-update ya no lo ejecuta el worker: un updater root activado por `.path` verifica firma, valida el tarball, instala inmutable, y hace rollback (incluida la BD).
- Unidades systemd endurecidas (`systemd-analyze security` 8.4 → 1.5, medido offline), Redis por socket Unix con JSON, usuario de Node sin acceso a secretos, CSP con nonce, Caddy con límites.
- **Verificado en tres niveles distintos** (ver §3): simulado con binarios falsos, ejecutado en CI con systemd real (Debian 12, **sin PVE**) y **no verificado**: todo lo que necesita un PVE real (`LAB-CHECKLIST.md`).
- **Pendiente humano:** clave de firma (`deploy/release-signers` sigue con el marcador), ajustes de GitHub, primera release firmada y validación en laboratorio.

## 2. Hallazgos
| ID | Sev. | Estado | Test que lo demuestra | Commit(s) |
|---|---|---|---|---|
| F-01 | Alta | cerrado (canal opt-in; riesgo residual §6) | `deploy/tests/test_ssh_gate.sh` (109), `test_install_community.sh` (50: claves hostiles, idempotencia, revocación), `backend/tests/test_ssh_gate_client.py`, `test_ssh_preflight.py`, `test_provisioning.py` (tag+sin privilegios), `test_catalog.py` (409 canal apagado) | 7d156d5 c4a0a59 3ea9203 7011baf 0755230 |
| F-02 | Alta | cerrado | `test_bootstrap.sh` (release `root:root`, sin g/o-w, `chown` solo a root, pip sin `-e`), `test_units.sh`, `test_updater.sh` (release nueva inmutable) **[CI]** `smoke-systemd.sh` (usuario de servicio no puede escribir en /opt ni unidades) | b3bbd73 0389b53 |
| F-03 | Alta | cerrado | `backend/tests/test_selfupdate.py` (worker sin subprocess/sudo, estados), `test_updater.sh` (281→348), `test_units.sh` (sin sudo), **[CI]** updater real activado por `.path` | 0389b53 93492a0 0698056 |
| F-04 | Alta | cerrado | `test_install_verify.sh` (firma inválida/otra clave/otro namespace, hash, tag inexistente), `test_updater.sh`, `test_release_sign.sh`, `test_set_fork.sh` (sin upstream ejecutable), `test_workflows.sh` | 7011baf 0389b53 57d9761 24dd6ed 79c1d8c |
| F-05 | Alta | cerrado | `test_updater.sh`: tags hostiles ×17, nunca sobrescribe activo/previo, rollback con BD restaurada solo si migró, downgrade, health | 0389b53 0698056 |
| F-06 | Alta | cerrado | `backend/tests/test_setup.py` (sin token/erróneo/correcto/409, rate limit, fail-closed), `api-client.test.ts`, **[CI]** wizard con token real | f0a806f |
| F-07 | Media | cerrado | `test_install_args.sh` (cpu/ram/disk/storage/bridge/repo/release/ip hostiles), `test_bootstrap.sh` (tag hostil; sin `bash -c` con interpolación) | 7011baf b3bbd73 |
| F-08 | Media | cerrado | `test_install_args.sh` (HOSTNAME, CTID ajeno, marcador, `--flag` sin valor) — rojo original documentado | 7011baf |
| F-09 | Media | cerrado | `test_units.sh` (usuario web separado, `InaccessiblePaths`), `test_bootstrap.sh`, `backend/tests/test_redis_conf.py` (socket, JSON, pickle rechazado, ida y vuelta real), **[CI]** Redis sin TCP, web sin acceso a secretos | b3bbd73 591f4a8 |
| F-10 | Media | cerrado (build de Vite no reproducible: aceptado, X17) | `test_bootstrap.sh` (pins con SHA-256, sin `curl\ | sh`, sin upgrade de pip), CI (lock al día, scans bloqueantes), `build-release.sh` (validado con el validador del updater), `markdown*.test.ts` |
| F-11 | Media | cerrado | `test_ssh_gate_client.py` (argv exacto, nodo hostil no lanza proceso), `test_install_community.sh` (host key/known_hosts) | c4a0a59 7011baf |
| F-12 | Media | cerrado | `test_units.sh` (directivas), `test_caddy_render.sh` (IP vigente, allow-list, CSP), `test_http_surface.py`, `test_ws_origin.py`, **[CI]** CSP con nonce, Host ajeno 400, sandbox real | b3bbd73 fbc7ae2 333f4d3 1389a98 |
| F-13 | Media | cerrado | `test_console_pinning.py` (servidor wss real), `test_cluster_target_policy.py` (83), `test_community_script_command.py` (77), `test_regex_anchoring.py` | 77d03c1 55baedb 07aeab7 |
| F-14 | Baja | cerrado | `test_install_community.sh` (desinstalación ida y vuelta), `test_bootstrap.sh` (sin `/opt/proxmox-gui-src`), gitleaks bloqueante | 7011baf b3bbd73 |
| F-15 | Alta | cerrado | `test_updater.sh`: symlinks plantados en `request`, `update/`, `.restore`, `-wal/-shm`, `backups`, `app.db` (fichero víctima intacto) | 0698056 |
| F-16 | Media | cerrado | `test_ssh_gate.sh`: inyección de líneas/escapes, TERM→KILL con proceso que ignora TERM | 0755230 |
| F-17 | Media | cerrado | `test_updater.sh`: hardlink, duplicados, `.`, `//`, `\`, symlinks, dispositivos, FIFO, setuid, mismo intérprete valida y extrae | 0698056 |
| F-18 | Media | cerrado | `test_regex_anchoring.py` (por los puntos de llamada reales), `test_community_script_command.py` | 07aeab7 |
| A1–A5 (APP-REVIEW) | Alta/Media | cerrado | `test_authz_review.py` (+ inventario de rutas), `test_redis_conf.py` | 4129518 f0a806f |

Detalle y mapa de pruebas: `FINDINGS-TESTS.md`. Revisión de la app (autorización, jobs, Redis): `APP-REVIEW.md`.

## 3. Evidencia de verificación

### 3.1 Suite y comparación con `BASELINE.md`
| Comprobación | Línea base (e485bc4) | Final |
|---|---|---|
| Backend `pytest -q` | 623 passed | **953 passed** |
| Frontend `pnpm test` | 382 passed | **385 passed** (26 archivos) |
| Frontend `pnpm check` | 0 errores | 0 errores |
| Tests de shell `deploy/tests` | no existían | 11 suites (≈970 aserciones), también como uid 65534 |
| ruff | 32 errores | 25 (ratchet `RUFF_MAX=25`, solo puede bajar) |
| mypy | 90 errores | 90 (sin regresión) |
| `scripts/check.sh` | — | **OK** (incluye shellcheck, perl -c, deploy tests, ratchet) |

Cobertura: la línea base no midió cobertura y `pytest-cov` no está en el entorno, así que **no hay comparación de cobertura**; se sustituye por el aumento de tests (+330 backend) y las pruebas negativas por hallazgo. Desviación anotada en §5.

### 3.2 Escaneos antes/después (`SCANS-BASELINE.md` → `SCANS-FINAL.md`)
gitleaks 13 → 0; bandit 3 Medium → 0 (0 High); pip-audit 28 avisos → 0; `pnpm audit --prod` 33 (6 high) → 0. Bloqueantes en CI.

### 3.3 Endurecimiento systemd
`systemd-analyze security` (offline, sobre los ficheros de unidad): 8.4 → 1.5 para la API. Medición real dentro de CI: api 1.5, worker 1.5, frontend 1.5 (umbral del test ≤ 3.0), medido en el job `smoke-systemd` (Debian 12, systemd real).

### 3.4 Qué es simulado, qué es CI con systemd real y qué NO está probado
| Nivel | Qué cubre | Dónde |
|---|---|---|
| **Simulado** | `install.sh` con `pct`, `pveam`, `ssh`, `ssh-keygen`, `gh`, `caddy`… falsos; el updater con árboles temporales (`PGUI_ROOT`); el gate SSH con hooks de entorno; el cliente SSH del backend con un servidor falso | `deploy/tests/*.sh` (11 suites), `backend/tests` |
| **CI con systemd real (Debian 12, sin PVE)** | `bootstrap.sh` sobre un tarball de release real; unidades reales arrancan; sandboxing efectivo; Redis solo por socket; `/api/v1/health` por Caddy; `/setup` exige token; CSP con nonce; Host ajeno rechazado; updater real activado por `.path` | job `smoke-systemd` de `ci.yml` (`deploy/tests/smoke-systemd.sh`) — **verde en Actions** (run 36707240697, 64 comprobaciones; los 5 jobs en verde) |
| **NO verificado (requiere PVE real)** | `pct create` con plantillas, `pveam`, `pct exec` real, redes/almacenamiento, `from=` con IP real, MemoryDenyWriteExecute/RestrictAddressFamilies en Node dentro de un LXC sin privilegios, PrivateDevices en LXC, rollback sobre un CT real, desinstalación real y `authorized_keys` | `LAB-CHECKLIST.md` |

## 4. Decisiones tomadas por defecto
Las decisiones D1–D12 de `PLAN.md` se aplicaron tal cual (todas `[REVISAR]`):

| ID | Decisión | Por defecto | Alternativa descartada |
|---|---|---|---|
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

Adicionales (X1–X17, en el orden de `DECISIONS.md`; las más impactantes: X7 clave de firma incrustada en `install.sh`, X8 `--update` exige `--ctid`, X9 layout plano del tarball, X13 estado del updater en `/run`, X14 el updater no actualiza Node/Python, X15 clave de firma del updater):

| ID | Decisión | Motivo |
|---|---|---|
| X1 | La rama de trabajo es `hardening/main`, pedida por el usuario en el prompt (la sesión trae asignada `claude/youthful-edison-l5plf7`); se empuja solo a `origin` = `dakser/proxmox-gui` `[REVISAR]` | Instrucción explícita del usuario |
| X2 | `scripts/check.sh` aplica un *ratchet* a ruff/mypy (no pueden empeorar respecto a `BASELINE.md`) y usa `pnpm check` en lugar de `pnpm lint` (ESLint roto en la base) `[REVISAR]` | Falla en la línea base, no atribuible al plan |
| X3 | Herramientas instaladas en un venv fuera del repo (`/tmp/...`); `gitleaks` 8.24.3 binario oficial | Entorno de sesión |
| X4 | Pasos `bandit`/`pip-audit`/`pnpm audit` del CI son `continue-on-error` hasta actualizar dependencias vulnerables; gitleaks sí bloquea `[REVISAR]` | La línea base ya tiene avisos (SCANS-BASELINE.md); bloquear el CI ahora lo dejaría rojo sin remedio en P0 |
| X5 | `deploy/tests/EXPECTED-RED`: un test rojo documentado no rompe `run.sh`, pero si pasa sin quitarlo de la lista el run falla | Cumple "prueba roja documentada" de P0-05 sin `\|\| true` |
| X6 | Nombres de variables de entorno del gate: `^[A-Za-z_][A-Za-z0-9_]*$` (no solo mayúsculas) con denylist; las variables se entregan dentro del CT con `env K=V`, no al proceso `pct` del host `[REVISAR]` | community-scripts exige `app` y `tz` en minúscula (`_build_install_env`); poner env en `pct` (root en el host) permitiría `PERL5OPT` |
| X7 | La clave de firma se incrusta en `deploy/install.sh` (bloque generado por `scripts/sync-signers.sh` desde `deploy/release-signers`); `--signers FILE` la sustituye. Los marcadores (`REEMPLAZAR-CON-TU-CLAVE-PUBLICA`, `TODO-PIN`) hacen fallar el instalador y `scripts/check.sh`, salvo `PGUI_ALLOW_PLACEHOLDERS=1` (lo pone el CI para que el fork pueda correrlo antes de tener clave) `[REVISAR]` | El install.sh descargado es el único ancla de confianza: leer `release-signers` del mismo repo que se quiere verificar no protegería nada |
| X8 | Un CTID existente sin `--update` aborta (antes entraba solo en la ruta de actualización). `--update` requiere `--ctid` explícito `[REVISAR]` | F-08: evitar actuar sobre CT ajenos |
| X9 | El tarball de release tiene layout plano (`backend/ frontend/ deploy/ requirements.lock …` sin directorio raíz) y nombre `proxmox-gui-<tag>.tar.gz`; el host solo extrae `deploy/host/proxmox-gui-ssh-gate` `[REVISAR]` | Extracción mínima y predecible en el host |
| X10 | El nodo se entrega al CT con `/etc/hosts` (nombre corto/FQDN → IP del nodo vista desde el CT) además de known_hosts; solo cubre el nodo local que hospeda el CT `[REVISAR]` | El cliente usa el nombre de nodo del API PVE; sin resolución `StrictHostKeyChecking=yes` no conectaría |
| X11 | Subidas de dependencias backend hechas por los avisos de pip-audit (cryptography 50, pyjwt 2.14, python-multipart 0.0.31, pydantic-settings 2.14.2); la suite completa sigue verde `[REVISAR]` | Cerrar los 28 avisos de SCANS-BASELINE.md; no hubo regresiones |
| X12 | El `frontend/build/` versionado incluía su propio `build/node_modules` (adapter-node deja `@sveltejs/kit` etc. como dependencias externas). La release compilada en CI (P6-02) debe llevar las dependencias de producción (`pnpm install --prod --frozen-lockfile` en `frontend/`, resolubles desde `frontend/node_modules` o `build/node_modules`) `[REVISAR]` | Sin ellas `node build/index.js` no arranca (comprobado al reconstruir); es una razón más por la que el build versionado no era verificable (F-10) |
| X13 | El estado del updater vive en `/run/proxmox-gui-updater/status.json` (directorio de root) y no en `/var/lib/proxmox-gui` (de la app): así la app no puede sustituir ni borrar el archivo que refleja al updater `[REVISAR]` | Integridad de la señal; se pierde al reiniciar el LXC (solo informativo) |
| X14 | El updater no actualiza Node/Python: si `pins.env` de la release cambia el hash del toolchain, se detiene con instrucciones (reinstalar con `install.sh`) `[REVISAR]` | Evita instalar binarios de toolchain desde una ruta no interactiva; alcance de P4 |
| X15 | La clave de firma del updater es la que `install.sh` usó para verificar la instalación (se entrega al LXC); rotarla = reinstalar o editar `/etc/proxmox-gui/release-signers` a mano `[REVISAR]` | Una release firmada no puede cambiar quién puede firmar |
| X16 | Si el worker se reinicia a mitad de una actualización (p. ej. restauración de BD tras un fallo), el job queda huérfano y el reaper lo marca `needs_review` `[REVISAR]` | Comportamiento ya existente del reaper para jobs sin UPID |
| X17 | Reproducibilidad: solo el empaquetado es determinista; el build de Vite/SvelteKit no lo es (hashes de chunk cambian entre ejecuciones). No se promete "build reproducible"; la garantía es release firmada por ti + build en CI desde lockfile `[REVISAR]` | Comprobado construyendo dos veces el mismo commit |

## 5. Desviaciones del plan
- **Cobertura (P7-01):** no medida (ni en la base ni ahora); sustituida por tests nuevos y pruebas por hallazgo. Se recomienda añadir `pytest-cov` a CI como tarea futura.
- **`pnpm lint`:** falla también en la base (ESLint sin config plana); se usa `pnpm check` + ratchet (X2).
- **Escaneos en CI:** inicialmente `continue-on-error` (X4); vueltos bloqueantes en P6-06 tras subir dependencias.
- **Build del frontend no reproducible** (Vite): solo el empaquetado es determinista (X17). La integridad la da la firma sobre el tarball, no la reproducibilidad.
- **Prueba de humo local (P7-02):** no pudo ejecutarse en la sesión de Claude (la red bloquea `deb.debian.org`, ver `BLOCKED.md`); se ejecuta solo en GitHub Actions.
- **Tres runs de CI fallaron sin que los mirara** al inicio (causas reales, corregidas: `install -o root` sin root, ruta del lock, etc.); `check.sh` ahora repite los tests de shell como usuario sin privilegios.
- **`.planning/` no se toca** (D12): contiene IPs del laboratorio del autor original.

## 6. Riesgos residuales
- Una app comprometida puede descifrar los tokens de PVE: `master.key` y la BD comparten host y la app necesita ambos.
- Con `--enable-community-scripts`, el canal sigue permitiendo ejecutar código **dentro de los CT con tag** (por diseño); el riesgo residual es la integridad del script fijado por commit y de la tag.
- La clave de firma es un único punto de confianza; su compromiso permite publicar releases que el updater aceptará. Guárdala offline.
- Toolchain (Node/Python) fijado por SHA-256 pero descargado de sus orígenes en la instalación; el updater no lo actualiza (X14).
- El build de Vite no es reproducible (X17).
- Lo de §3.4 "NO verificado": comportamientos dependientes de PVE/LXC reales pueden requerir relajar una directiva de sandboxing concreta.
- Dependencias de desarrollo con 7 avisos que no viajan en la release.

## 7. Lo que debes hacer tú
1. **Clave de firma de releases (D2/D3).** Genera `ssh-keygen -t ed25519 -f ~/.ssh/proxmox-gui-release`, pon la línea pública en `deploy/release-signers` (formato `proxmox-gui-release namespaces="proxmox-gui-release" ssh-ed25519 AAAA…`) y ejecuta `scripts/sync-signers.sh`. Hasta entonces `install.sh` se niega a instalar y `scripts/check.sh` falla (salvo `PGUI_ALLOW_PLACEHOLDERS=1`).
2. **Fork.** `scripts/set-fork.sh dakser/proxmox-gui` ya está aplicado; verifica con el `grep` de P6-04 (`docs/hardening/PLAN.md`) que no queda ninguna URL del autor original.
3. **Ajustes de GitHub (no se pueden hacer desde el repo):** activar 2FA en tu cuenta; proteger `main` (PR obligatorio, checks `shell`, `backend`, `frontend`, `scans`, `smoke-systemd` requeridos, sin force-push); activar Dependabot alerts y security updates; en *Settings → Actions → General* poner permisos del `GITHUB_TOKEN` en solo lectura y exigir aprobación para workflows de forks.
4. **Primera release.** Tras el punto 1: `git tag v0.1.0 && git push origin v0.1.0`; el workflow `release.yml` deja un borrador con tarball, `install.sh` y `SHA256SUMS`; fírmalo con `scripts/release-sign.sh v0.1.0 --publish` (re-comprueba los hashes antes de firmar).
5. **Validación en laboratorio** con `docs/hardening/LAB-CHECKLIST.md` (PVE anidado, snapshot previo). Es lo único que verifica lo que CI no puede: `pct create` con plantillas, `pct exec` real, MemoryDenyWriteExecute/RestrictAddressFamilies en Node, PrivateDevices en LXC sin privilegios, `from=` con IP real, rollback real.
6. **Revisar las decisiones marcadas `[REVISAR]`** en `DECISIONS.md` (D1–D12 y X1–X17) y confirmarlas o cambiarlas.
7. **Dependencias:** 7 avisos de `pnpm audit` en dependencias de desarrollo (vitest/js-yaml; no viajan en la release) y los avisos de Dependabot futuros; `requirements.lock`/`pnpm-lock` se regeneran con `scripts/update-lock.sh` y se revisan a mano.

## 8. Cómo instalar
Con `deploy/release-signers` ya con tu clave y la release firmada (pasos 1–4 de §7), en el nodo PVE (primero en laboratorio, con snapshot):

```
V=v0.1.0   # la versión publicada y firmada
R=https://github.com/dakser/proxmox-gui/releases/download/$V
curl -fsSLO $R/install.sh && curl -fsSLO $R/SHA256SUMS && curl -fsSLO $R/SHA256SUMS.sig
ssh-keygen -Y verify -f <(cat allowed-signers) -I proxmox-gui-release -n proxmox-gui-release -s SHA256SUMS.sig < SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
less install.sh                       # léelo antes de ejecutarlo
bash install.sh --release $V          # sin --enable-community-scripts por defecto
```
`allowed-signers` es tu clave pública (la misma línea que `deploy/release-signers`). Con community-scripts: añade `--enable-community-scripts`. Actualizar: `bash install.sh --update --ctid <id> --release <vX.Y.Z>`. Desinstalar: `bash install.sh --uninstall [--purge] --ctid <id>`. Detalle en `deploy/README.md`.
