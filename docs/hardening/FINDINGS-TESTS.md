# Hallazgo → prueba negativa → commit (P7-03)

Cómo se comprobó el "rojo antes / verde ahora":
- **Backend:** los ficheros de test nuevos se copiaron a un `git worktree` del commit base `e485bc4` y se ejecutaron contra el código original
  (resultado abajo). Un fichero que solo da *error de colección* falla porque el módulo probado no existía (rojo trivial, marcado `módulo nuevo`).
- **Shell (`deploy/tests`):** los componentes no existían en la base (gate, updater, firmas…), así que no hay "rojo contra la base" ejecutable salvo
  `test_install_args.sh`, cuyo rojo contra el `install.sh` original está registrado en LOG (P0-05: 7 aserciones fallidas) y pasó a verde en P2-01. Para el resto se hizo
  **mutación**: se rompió a propósito el control (p. ej. validador de tarball, condición de restauración de BD) y los tests fallaron (LOG P4-02).
- Los tests de comportamiento real (no simulado) están marcados **[CI]** — los ejecuta el job `smoke-systemd` con systemd real (ver HARDENING-REPORT §3).

## Resultado contra la base (backend)

| Fichero de test | Contra `e485bc4` | Ahora |
|---|---|---|
| `test_setup.py` (token) | 6 fallan, 11 errores | verde |
| `test_ws_origin.py` | 2 fallan (los decisivos: cookie válida + Origin ajeno) | verde |
| `test_authz_review.py` | 4 fallan (A1 jobs sin equipo, A2 jobs de sistema…) | verde |
| `test_regex_anchoring.py` | 7 fallan | verde |
| `test_console_pinning.py` | 7 fallan (el token llegaba a un servidor con otro certificado) | verde |
| `test_http_surface.py` | 15 fallan (docs públicas, sin Host allow-list) | verde |
| `test_cluster_target_policy.py` | módulo nuevo | verde |
| `test_community_script_command.py` | 3 fallan (`"docker\n"`, `"<sha>\n"`) | verde |
| `test_redis_conf.py` | módulo nuevo | verde |
| `test_cipher.py` | 2 fallan (master.key 0440) | verde |
| `test_selfupdate.py` | 3 fallan + 27 errores | verde |
| `test_ssh_gate_client.py`, `test_ssh_preflight.py` | módulo nuevo / errores | verde |

## Tabla

| ID | Sev. | Pruebas que lo demuestran | Commits |
|----|------|---------------------------|---------|
| F-01 | Alta | `deploy/tests/test_ssh_gate.sh` (109), `test_install_community.sh` (50: claves hostiles, idempotencia, revocación), `backend/tests/test_ssh_gate_client.py`, `test_ssh_preflight.py`, `test_provisioning.py` (tag+sin privilegios), `test_catalog.py` (409 canal apagado) | 7d156d5 c4a0a59 3ea9203 7011baf 0755230 |
| F-02 | Alta | `test_bootstrap.sh` (release `root:root`, sin g/o-w, `chown` solo a root, pip sin `-e`), `test_units.sh`, `test_updater.sh` (release nueva inmutable) **[CI]** `smoke-systemd.sh` (usuario de servicio no puede escribir en /opt ni unidades) | b3bbd73 0389b53 |
| F-03 | Alta | `backend/tests/test_selfupdate.py` (worker sin subprocess/sudo, estados), `test_updater.sh` (281→348), `test_units.sh` (sin sudo), **[CI]** updater real activado por `.path` | 0389b53 93492a0 0698056 |
| F-04 | Alta | `test_install_verify.sh` (firma inválida/otra clave/otro namespace, hash, tag inexistente), `test_updater.sh`, `test_release_sign.sh`, `test_set_fork.sh` (sin upstream ejecutable), `test_workflows.sh` | 7011baf 0389b53 57d9761 24dd6ed 79c1d8c |
| F-05 | Alta | `test_updater.sh`: tags hostiles ×17, nunca sobrescribe activo/previo, rollback con BD restaurada solo si migró, downgrade, health | 0389b53 0698056 |
| F-06 | Alta | `backend/tests/test_setup.py` (sin token/erróneo/correcto/409, rate limit, fail-closed), `api-client.test.ts`, **[CI]** wizard con token real | f0a806f |
| F-07 | Media | `test_install_args.sh` (cpu/ram/disk/storage/bridge/repo/release/ip hostiles), `test_bootstrap.sh` (tag hostil; sin `bash -c` con interpolación) | 7011baf b3bbd73 |
| F-08 | Media | `test_install_args.sh` (HOSTNAME, CTID ajeno, marcador, `--flag` sin valor) — rojo original documentado | 7011baf |
| F-09 | Media | `test_units.sh` (usuario web separado, `InaccessiblePaths`), `test_bootstrap.sh`, `backend/tests/test_redis_conf.py` (socket, JSON, pickle rechazado, ida y vuelta real), **[CI]** Redis sin TCP, web sin acceso a secretos | b3bbd73 591f4a8 |
| F-10 | Media | `test_bootstrap.sh` (pins con SHA-256, sin `curl\|sh`, sin upgrade de pip), CI (lock al día, scans bloqueantes), `build-release.sh` (validado con el validador del updater), `markdown*.test.ts` | c187526 08cbbb6 57d9761 3c9deee |
| F-11 | Media | `test_ssh_gate_client.py` (argv exacto, nodo hostil no lanza proceso), `test_install_community.sh` (host key/known_hosts) | c4a0a59 7011baf |
| F-12 | Media | `test_units.sh` (directivas), `test_caddy_render.sh` (IP vigente, allow-list, CSP), `test_http_surface.py`, `test_ws_origin.py`, **[CI]** CSP con nonce, Host ajeno 400, sandbox real | b3bbd73 fbc7ae2 333f4d3 1389a98 |
| F-13 | Media | `test_console_pinning.py` (servidor wss real), `test_cluster_target_policy.py` (83), `test_community_script_command.py` (77), `test_regex_anchoring.py` | 77d03c1 55baedb 07aeab7 |
| F-14 | Baja | `test_install_community.sh` (desinstalación ida y vuelta), `test_bootstrap.sh` (sin `/opt/proxmox-gui-src`), gitleaks bloqueante | 7011baf b3bbd73 |
| F-15 | Alta | `test_updater.sh`: symlinks plantados en `request`, `update/`, `.restore`, `-wal/-shm`, `backups`, `app.db` (fichero víctima intacto) | 0698056 |
| F-16 | Media | `test_ssh_gate.sh`: inyección de líneas/escapes, TERM→KILL con proceso que ignora TERM | 0755230 |
| F-17 | Media | `test_updater.sh`: hardlink, duplicados, `.`, `//`, `\`, symlinks, dispositivos, FIFO, setuid, mismo intérprete valida y extrae | 0698056 |
| F-18 | Media | `test_regex_anchoring.py` (por los puntos de llamada reales), `test_community_script_command.py` | 07aeab7 |
| A1–A5 (APP-REVIEW) | Alta/Media | `test_authz_review.py` (+ inventario de rutas), `test_redis_conf.py` | 4129518 f0a806f |
