# Decisiones tomadas por defecto  ([REVISAR] = confirmar o cambiar al final)

Las decisiones D1–D12 del registro de `PLAN.md` se aplican tal cual (todas `[REVISAR]`). Decisiones adicionales:

| ID | Decisión | Motivo |
|---|---|---|
| X1 | La rama de trabajo es `hardening/main`, pedida por el usuario en el prompt (la sesión trae asignada `claude/youthful-edison-l5plf7`); se empuja solo a `origin` = `dakser/proxmox-gui` `[REVISAR]` | Instrucción explícita del usuario |
| X2 | `scripts/check.sh` aplica un *ratchet* a ruff/mypy (no pueden empeorar respecto a `BASELINE.md`) y usa `pnpm check` en lugar de `pnpm lint` (ESLint roto en la base) `[REVISAR]` | Falla en la línea base, no atribuible al plan |
| X3 | Herramientas instaladas en un venv fuera del repo (`/tmp/...`); `gitleaks` 8.24.3 binario oficial | Entorno de sesión |
| X4 | Pasos `bandit`/`pip-audit`/`pnpm audit` del CI son `continue-on-error` hasta actualizar dependencias vulnerables; gitleaks sí bloquea `[REVISAR]` | La línea base ya tiene avisos (SCANS-BASELINE.md); bloquear el CI ahora lo dejaría rojo sin remedio en P0 |
| X5 | `deploy/tests/EXPECTED-RED`: un test rojo documentado no rompe `run.sh`, pero si pasa sin quitarlo de la lista el run falla | Cumple "prueba roja documentada" de P0-05 sin `\|\| true` |
