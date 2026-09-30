# Bitácora de endurecimiento

| Fecha | ID | Resultado | Comando |
|---|---|---|---|
| 2026-09-30 | P0-01 | OK: python 3.12.3 (uv venv), node v22.22.2, pnpm 10.33, shellcheck 0.11.0, gitleaks 8.24.3, ruff 0.15.12, mypy, bandit, pip-audit, actionlint, ssh-keygen/sqlite3 (apt); `origin`=github.com/dakser/proxmox-gui (fork), rama `hardening/main` | `python --version; node --version; shellcheck --version` |
| 2026-09-30 | P0-02 | OK: BASELINE.md — pytest 623 passed (181 s), vitest 382 passed, svelte-check 0 errors; `pnpm lint` roto en la base (ESLint sin config plana); ruff 32 / mypy 90 errores preexistentes | `cd backend && pytest -q`; `cd frontend && pnpm test && pnpm check` |
| 2026-09-30 | P0-03 | OK: SCANS-BASELINE.md — gitleaks 13 (fixtures de tests), bandit 3 medium (B310), pip-audit 28 avisos, pnpm audit 33, IPs reales en .planning (no se toca, D12) | `gitleaks detect; bandit -r backend/app; pip-audit; pnpm audit --prod` |
| 2026-09-30 | P0-04 | OK: `scripts/check.sh` termina en 0 (ratchet ruff≤32, mypy≤90) | `scripts/check.sh` |
| 2026-09-30 | P0-05 | OK (rojo documentado): `deploy/tests/run.sh` — test_install_args.sh falla 7 aserciones contra el install.sh actual (HOSTNAME heredado, flag sin valor, CPU no numérico, CTID ajeno); listado en EXPECTED-RED hasta P2-01 | `deploy/tests/run.sh` |
| 2026-09-30 | P0-06 | OK: ci.yml + dependabot.yml, acciones fijadas por SHA, actionlint limpio; `.gitleaks.toml` (allowlist solo `backend/tests/`) → gitleaks: no leaks | `actionlint .github/workflows/ci.yml; gitleaks detect --config .gitleaks.toml` |

**Cierre P0:** línea base establecida (tests verdes, lint/mypy con ratchet), arnés de scripts de root con binarios simulados, CI mínimo y escaneos iniciales. Rojo documentado: `test_install_args.sh` (se resuelve en P2-01). Los pasos de auditoría del CI (bandit/pip-audit/pnpm audit) son `continue-on-error` hasta subir dependencias (decisión X4).
