# Línea base (commit e485bc4 + docs de hardening, antes de cualquier cambio)

Entorno: Python 3.12.3 (uv venv), Node v22.22.2, pnpm 10.33 (lockfile con pnpm 11.1.1 en salida), shellcheck 0.11.0.

| Comprobación | Comando | Resultado |
|---|---|---|
| Backend tests | `cd backend && pytest -q` | **623 passed**, 0 failed, 56 warnings, 181 s |
| Frontend tests | `cd frontend && pnpm install --frozen-lockfile && pnpm test` | **382 passed** (25 archivos), 4 s |
| Frontend tipos | `cd frontend && pnpm check` (svelte-check) | 0 errores, 0 warnings |
| Frontend lint | `cd frontend && pnpm lint` | **FALLA en la línea base**: ESLint 9 no encuentra `eslint.config.js` (usa `.eslintignore`/formato antiguo). No es regresión. |
| ruff | `cd backend && ruff check .` | **32 errores preexistentes** (21 autofixables) |
| mypy | `cd backend && mypy --config-file mypy.ini app` | **90 errores preexistentes** en 14 archivos |

## Fallos registrados (no cuentan como regresión)
- `pnpm lint` (ESLint sin config plana). `scripts/check.sh` usa `pnpm check` en su lugar y aplica un *ratchet*.
- ruff: 32 errores; mypy: 90 errores. `scripts/check.sh` falla si el número **aumenta** (ratchet en `scripts/baseline-counts.env`).
