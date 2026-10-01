# Revisión dirigida de la aplicación (P5-08)

Método: inventario automático de rutas (dependencias de auth/CSRF), lectura de los módulos citados en el plan buscando rutas sin auth, IDOR/tenant,
SQL crudo, `eval/exec`, deserialización insegura y secretos en logs. Todo hallazgo tiene un test que **falló antes del arreglo**
(`backend/tests/test_authz_review.py`, `test_regex_anchoring.py`). Sin cobertura por línea: es una revisión dirigida, no una auditoría completa.

## Hallazgos y arreglos

| # | Sev. | Dónde | Problema | Test | Estado |
|---|------|-------|----------|------|--------|
| A1 | **Alta** | `jobs/service.py::list_jobs` (y por tanto `GET /jobs` y el backfill de `/ws/jobs`) | `if team_ids:` omitía el filtro cuando el usuario no pertenecía a ningún equipo ⇒ veía los jobs de **todos** los tenants | `test_user_without_any_team_sees_no_jobs`, `…ws_backfill_set` | cerrado |
| A2 | Media | `jobs/routes.py` (`GET /jobs/{id}`, `POST /jobs/{id}/retry`) | jobs de sistema (`team_id` NULL: self-update, boot) visibles/reintentables por cualquier usuario autenticado | `test_system_jobs_are_admin_only` | cerrado (solo admin) |
| A3 | Media | `jobs/events.py::broadcast` | eventos sin equipo (self-update) se enviaban a **todos** los sockets | `test_system_job_events_reach_admin_sockets_only` | cerrado (solo sockets admin) |
| A4 | Media | 8 validadores | `re.match`+`$` aceptaba `"valor\n"` (slug/sha de community-scripts, usuario cloud-init → YAML, tags PVE, claves de disco, token_user, tag de update, PAT) | `test_regex_anchoring.py`, `test_community_script_command.py` | cerrado → F-18 |
| A5 | Media | `security/rate_limit.py` | cliente Redis TCP 6379 fijo: con Redis solo en socket caía en silencio a memoria de proceso | `test_rate_limiter_uses_the_unix_socket…` | cerrado (P5-01) |

## Revisado sin hallazgo

- **Autenticación**: solo 7 rutas sin dependencia de auth, todas esperadas y fijadas por `test_every_route_requires_authentication_except_the_public_allow_list`
  (login/refresh/keepalive/logout autentican con cookie de refresh; setup con token). Toda ruta mutante lleva `csrf_protect` salvo auth/setup (test).
- **auth/**: hash con Argon2 y `DUMMY_HASH` para igualar tiempos (usuario inexistente/desactivado); cookies `httpOnly`/`Secure`/`SameSite`; refresh rotativo con detección de reutilización.
- **pats/**: hash con pepper, comparación `secrets.compare_digest`; PAT solo en la cabecera Bearer.
- **ssh_keys/**, **me/**: consultas siempre filtradas por `user_id` del principal (sin IDOR).
- **audit/**: RBAC en servidor (no admin: solo lo suyo o el de sus equipos), export CSV con neutralización de fórmulas (`csv_safe`), descarga de archivos con guarda de traversal.
- **inventory/lifecycle/console**: `resolve_resource` valida pertenencia al equipo antes de cualquier acción (probado en `test_inventory_access.py`, `test_console.py`).
- **quotas/**: un no-admin solo previsualiza sus equipos; **backups** lista solo sus equipos (lista vacía → vacío).
- **SQL/ejecución**: sin `text()` con interpolación (solo `BEGIN IMMEDIATE` y `server_default`), sin `eval/exec/pickle/yaml.load/shell=True`; los únicos subprocess son `ssh` con argv en lista (P1) — el del updater ya no existe.
- **Logs**: ningún `logger.*` interpola contraseñas/tokens/tickets; el archivo del token de setup no se imprime.
- **mcp/**: cliente HTTP de la API con PAT; no accede a BD ni a Proxmox.

## Riesgos aceptados (documentados)

- `GET /clusters/{id}/nodes/resources` y `…/backup-storages` los puede pedir **cualquier usuario autenticado para cualquier `cluster_id`** y usan el conector de administración
  (nombres de nodos y capacidad libre; diseño T-04-16-01). Fuga acotada a metadatos de infraestructura, no a datos de tenants.
- `catalog` (lectura) no está ligado a un equipo: es información pública de community-scripts.
