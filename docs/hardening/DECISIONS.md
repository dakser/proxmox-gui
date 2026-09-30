# Decisiones tomadas por defecto  ([REVISAR] = confirmar o cambiar al final)

Las decisiones D1–D12 del registro de `PLAN.md` se aplican tal cual (todas `[REVISAR]`). Decisiones adicionales:

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
