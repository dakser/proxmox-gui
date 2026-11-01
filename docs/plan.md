# Plan v0.1.1 — fases, pruebas y salida

Cada fase tiene objetivo, qué leer, tareas, **pruebas con nombre** (y la mutación que las haría fallar) y salida. Solo el
humano cambia este plan. Las pruebas de aceptación viven en `backend/tests/acceptance/` y `frontend/tests/acceptance/`
(o `deploy/tests/acceptance/` para el instalador); las escribe el autor de pruebas **antes** del código y el implementador no las
toca. Las puertas están en `gates.toml`; `tests/acceptance/manifest.toml` lista cada prueba con su regla, lo que demuestra y su mutación,
y `scripts/accept_check.py` exige que existan con ese nombre exacto. El inventario con evidencia está en `docs/hardening/V0.1.1-PLAN.md`.

Línea base: la suite existente completa entra en todas las puertas como regresión (backend `pytest -q`, frontend
`pnpm check && pnpm test && pnpm build`, `scripts/check.sh`, `deploy/tests/run.sh`).

## F0 — Cadena de suministro y CI (L-00)

- **Leer:** `scripts/update-lock.sh`, `docs/hardening/SCANS-FINAL.md`, `backend/pyproject.toml`.
- **Tareas:** adelantar la fecha de corte de `--exclude-newer` con su motivo; regenerar el lock; evaluar cada CVE; registrar
  la conclusión.
- **Pruebas:** `test_lock_has_no_known_vulns` (la puerta ejecuta `pip-audit` sobre el lock) · mutación: bajar `certifi` a la
  versión vulnerable y comprobar que falla.
- **Salida:** `scans` en verde en `master`. Reglas: R-01, R-02.

## F1 — Sesión y acceso (L-01, L-02, L-03, L-09, L-12)

- **Leer:** `frontend/src/hooks.server.ts`, `frontend/src/routes/+layout.server.ts`, `backend/app/auth/`,
  `backend/app/users/service.py`, `backend/app/teams/bootstrap.py`, `backend/app/clusters/service.py`,
  `frontend/src/routes/create/+page.svelte`, `frontend/src/lib/components/wizard/lxc-wizard.ts`.
- **Tareas:** renovación de sesión desde el servidor; provisionar al crear usuario o equipo; botón de reparación; equipo
  por defecto del asistente; botón con razón; detalle de `403`/`409`; pestaña de miembros; reducir ruido en `/setup`.
- **Pruebas:**
  - `test_ssr_refreshes_expired_access_cookie` (R-03): cookie de acceso caducada + refresh vigente → no hay `303`. Mutación:
    quitar la rama de renovación → debe fallar.
  - `test_ssr_redirects_when_refresh_expired_or_idle` (R-04): refresh caducado o inactividad → `303` a `/login?next=`.
    Mutación: renovar siempre → debe fallar.
  - `test_refresh_rotates_single_use` (R-05). Mutación: permitir reutilizar el refresh → debe fallar.
  - `test_create_user_bootstraps_all_active_clusters` y `test_create_user_rolls_back_on_pve_failure` (R-07). Mutación: quitar la
    limpieza de Proxmox → debe fallar (el doble PVE simulado debe quedar con restos).
  - `test_backfill_is_idempotent_and_exposed_in_ui` (R-08).
  - `test_wizard_defaults_to_team_with_token`, `test_create_button_disabled_with_reason`, `test_create_error_shows_detail`
    (R-09, R-10, R-11). Mutación: volver al equipo personal por defecto → debe fallar.
  - `test_team_members_tab_lists_and_edits` (R-12).
  - `test_setup_and_login_make_no_failing_requests` (R-06).
  - **Laboratorio (nivel 1):** `lab/scenarios/f1_new_user_can_provision.sh`: crear usuario nuevo, entrar, ver el clúster, crear un
    LXC pequeño en el pool de su equipo; limpiar. Falla si el usuario no ve clúster.
- **Salida:** un usuario nuevo llega al paso de creación con un equipo válido, y la sesión sobrevive a más de 15 minutos con
  recarga completa. Reglas: R-03 … R-12, R-06.

## F2 — Redes, permisos y mensajes (L-04, L-05, L-06, L-07, L-08)

- **Leer:** `backend/app/networks/service.py`, `backend/app/clusters/connector.py`, `backend/app/clusters/service.py`,
  `frontend/src/routes/admin/clusters/`, `docs/hardening/BRIEF-OPENCODE.md` §7.
- **Precondición:** el resultado del `curl` de diagnóstico de L-04 (lo pone el humano en la nota de la fase).
- **Tareas:** registrar los fallos de lectura de redes; distinguir "sin redes" de "no se pudo leer"; ajustar el rol según el
  diagnóstico; documentarlo y mostrarlo en el formulario; registrar el fallo de aprovisionamiento; huella y verificación;
  mensaje de destino no permitido.
- **Pruebas:**
  - `test_list_bridges_logs_and_reports_read_failure` (R-14). Mutación: volver a `except Exception: continue` sin registro → falla.
  - `test_network_picker_lists_vmbr0_with_documented_role` (R-13), con el doble PVE y, en el laboratorio,
    `lab/scenarios/f2_networks_listed.sh`.
  - `test_bootstrap_failure_is_logged_at_error` (R-16). Mutación: quitar el registro → falla.
  - `test_fingerprint_disables_chain_check_or_explains` (R-17), `test_unverified_tls_without_fingerprint_warns` (R-18),
    `test_policy_rejection_message_is_not_a_network_error` (R-19).
  - **Laboratorio:** `lab/scenarios/f2_full_provision.sh`: registrar clúster con el rol documentado, crear equipo, crear LXC y VM.
    Falla si hace falta cualquier privilegio no documentado (R-15).
- **Salida:** con el rol documentado, el flujo completo funciona en `prox-t1`. Reglas: R-13 … R-19.

## F3 — Instalador y empaquetado (L-10, L-11, L-13, L-14, L-15, L-16)

- **Leer:** `deploy/install.sh`, `deploy/lxc/bootstrap.sh`, `deploy/caddy/Caddyfile.template`, `scripts/build-release.sh`,
  `backend/app/setup/routes.py`.
- **Tareas:** deshabilitar sshd sin community-scripts; invalidar el token de setup; locale; `skip_install_trust` de Caddy; texto
  del panel SSH; tarball solo con dependencias de ejecución.
- **Pruebas:** `deploy/tests/acceptance/test_install_no_sshd.sh` (R-21), `backend/tests/acceptance/test_setup_token.py`
  (`test_setup_token_removed_after_admin`, R-22), `deploy/tests/acceptance/test_bootstrap_locale_and_caddy.sh` (R-23),
  `deploy/tests/acceptance/test_tarball_has_no_dev_deps.sh` (R-24). Mutación de
  cada una: revertir el cambio → debe fallar.
- **Salida:** `smoke-systemd` en verde con las pruebas nuevas; nivel 2 (humano) en el LXC 200. Reglas: R-21 … R-24, R-25.

## F4 — Release v0.1.1

- **Tareas:** alinear la versión (el paquete declara `0.6.3` en `backend/pyproject.toml` y `frontend/package.json`, y `/api/v1/health` lo
  reporta, mientras la release del fork es `v0.1.0`; el archivo `VERSION` del tarball lleva la etiqueta); `CHANGELOG.md`; prerelease `v0.1.1-rc1` firmada por el humano; pruebas de
  actualización v0.1.0 → rc1 (éxito) y con una release rota a propósito (rollback); firma inválida rechazada.
- **Salida:** regla R-26 y R-27 verificadas en el laboratorio; humano publica `v0.1.1`.

## Después: v0.2.0 (fuera de este plan)

Las mejoras M-01 … M-08 de `docs/hardening/V0.1.1-PLAN.md` se especifican en `docs/spec/v0.2.0.md` cuando termine F4, con su
propio plan por fases. No se empiezan antes.
