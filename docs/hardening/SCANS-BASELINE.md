# Escaneos de la línea base

| Herramienta | Resultado | Severidad | Decisión |
|---|---|---|---|
| gitleaks (51 commits, 34 MB, historial completo + `.planning/`) | 13 `generic-api-key`, **todos en `backend/tests/*`** (fixtures de contraseñas de prueba). Ninguno en `.planning/` ni en código de aplicación | Baja | Aceptar (fixtures). Se añade `.gitleaks.toml` con allowlist acotada a `backend/tests/` en P0-06 |
| bandit -r backend/app | 0 High, 3 Medium (B310 `urlopen` en `selfupdate/service.py:68,93` y `jobs/selfupdate_functions.py:121`), 10 Low | Media | Corregir: el código afectado se reescribe en P4-04 |
| pip-audit (`pyproject.toml` compilado, 28 avisos) | pyjwt 2.12.1 (14 avisos, fix 2.14.0), cryptography 46.0.7 (7), python-multipart 0.0.27 (6, fix 0.31), pydantic-settings 2.14.1 (1) | Media (no evaluada individualmente) | Reportar; subir versiones al generar `requirements.lock` (P3-06) si los tests siguen verdes; si no, registrar en DECISIONS.md |
| pnpm audit --prod | 33 avisos (6 high: devalue, vite, nanoid x2, postcss, joi; 20 moderate; 7 low) | Alta/Media | Reportar; actualización de dependencias del frontend en P6 (build en CI) — el tarball se compila con lockfile, ver DECISIONS |
| Hostnames/IPs reales en `.planning/` y `docs/` (excl. hardening) | 33 archivos con IPs RFC1918 (192.168.20.x, 192.168.42.42, 172.17.0.2…), **2 IPs públicas** (85.130.157.152, 212.211.160.228, 87.139.158.187), dominio `lab.local` | Baja (info del laboratorio del autor original) | Reportar (D12: no se toca `.planning/`) |
| `.planning/` | 190 archivos versionados | — | D12: no se toca |
