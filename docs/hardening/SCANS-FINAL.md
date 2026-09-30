# Escaneos finales (P7-04)

Repetición de P0-03 sobre el HEAD de `hardening/main`. En CI los cuatro escaneos son bloqueantes (job `scans`).

| Herramienta | Línea base | Final | Comentario |
|---|---|---|---|
| gitleaks (88 commits, 35 MB, con `.gitleaks.toml`) | 13 `generic-api-key` en `backend/tests/*` | **0** | La allowlist acotada a fixtures de tests elimina los 13; nada nuevo |
| bandit -r backend/app | 0 High, 3 Medium (B310), 10 Low | **0 High, 0 Medium**, 10 Low | B310 eliminados al reescribir el self-update (P4-04). Los 2 B608 de `rotate_master_key.py` llevan `# nosec B608` (identificadores de tabla/columna tomados de los metadatos del ORM, no de entrada) |
| pip-audit sobre `requirements.lock` | 28 avisos (pyjwt, cryptography, python-multipart, pydantic-settings) | **0** | Versiones subidas al generar el lock con hashes (P3-06) |
| pnpm audit --prod | 33 avisos (6 high) | **0** | `pnpm update` dentro de rangos + override `cookie ^0.7.2` (P6-06). Dev-deps: 7 avisos no bloqueantes que no viajan en la release |
| Hostnames/IPs reales en `.planning/` | 33 archivos | sin cambios | D12: no se toca `.planning/` (información del laboratorio del autor original) |

Ningún hallazgo nuevo de severidad ≥ media. Los 10 Low de bandit son los mismos de la línea base (asserts/`subprocess` con argumentos fijos, etc.).
