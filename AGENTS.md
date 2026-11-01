# AGENTS.md — reglas de trabajo para agentes

Este archivo es el que leen OpenCode y cualquier otro agente que no sea Claude Code. `CLAUDE.md` describe el proyecto y
sus restricciones de Proxmox (no se pueden saltar). Aquí van las reglas del **trabajo autónomo**. Solo las cambia el
humano.

Material de este kit: `docs/spec/v0.1.1.md` (qué debe pasar), `docs/plan.md` (fases, pruebas con nombre, mutaciones),
`gates.toml` (puertas), `docs/hardening/DECISIONS.md` (decisiones), `docs/hardening/BRIEF-OPENCODE.md` (entorno y
credenciales) y `docs/hardening/V0.1.1-PLAN.md` (inventario de hallazgos con su evidencia).

## Reglas

1. **Sala limpia.** Se lee la especificación y este repositorio. Nada de código de terceros que la especificación no
   autorice. Nada de criptografía escrita a mano: solo las bibliotecas que el proyecto ya usa.
2. **Duda = decisión registrada.** Ante una ambigüedad, el agente elige la opción conservadora, la anota como
   `PROVISIONAL` en `docs/hardening/DECISIONS.md` (con su impacto: *operación* o *forma interna*) y sigue. Nunca cambia
   la especificación ni una puerta.
3. **Cada prueba nueva lleva su mutación.** Al cerrar una tarea, el implementador rompe a propósito la regla que fija cada
   prueba (la mutación está en `tests/acceptance/manifest.toml`), confirma que la prueba falla, **restaura** el código y anota
   `MUTACIÓN <prueba>: detectada` en `docs/BLOCKERS.md`. Si la prueba no falla, `CULPA: prueba`. El orquestador exige esas
   líneas (`scripts/accept_check.py --mutations`) y el revisor comprueba que sean ciertas.
4. **Trazabilidad al día.** Cada regla cerrada tiene su fila en `docs/TRACEABILITY.md` con la prueba que la cubre. Una
   regla sin prueba no se presenta como probada.
5. **Economía.** Al empezar, `date` y un plan escrito. Mientras se trabaja, solo las pruebas del módulo tocado; la
   verificación completa una vez al final. Sin copias del repositorio y, al terminar, ningún proceso propio vivo.
6. **Bitácora inmediata.** Cada resultado se anota en `docs/BLOCKERS.md` en cuanto existe, bajo la sección de su ronda.
7. **Sin código muerto ni temporales sin dueño.** Lo marcado `TEMPORAL` dice qué fase lo retira.
8. **Informe de revisión con formato fijo.** Primera línea `VEREDICTO: APROBADO` o `VEREDICTO: RECHAZADO`; reparos
   numerados con archivo, regla, prueba de que falta y qué debe pasar; notas que no bloquean, aparte.
9. **Culpa explícita.** Si el agente cree que falla la prueba y no el código, lo dice en una línea marcada
   (`CULPA: prueba`), nunca en prosa. Las pruebas de aceptación no las toca el implementador.
10. **Roles separados.** El implementador no modifica las pruebas de aceptación (`backend|frontend|deploy tests/acceptance`,
    `tests/acceptance`), `lab/`, `gates.toml`, `docs/spec/`, `docs/plan.md`, `Makefile`, `.github/` ni el kit. El autor de pruebas
    solo escribe pruebas de aceptación y escenarios de laboratorio. Los revisores no modifican código: solo escriben su informe.

## Reglas propias de este proyecto

- Ningún cambio puede **debilitar un control del endurecimiento**: firma de releases (`deploy/release-signers`,
  `install.sh`), usuarios y permisos separados, directivas de `deploy/systemd/*`, CSP de `deploy/caddy/Caddyfile.template`, política
  de destinos (`backend/app/clusters/target_policy.py`), límite de intentos del setup y privilegios separados de los
  tokens por equipo. Si un punto lo exigiera: se para y se pregunta.
- Nada de "super-tokens". Un permiso de Proxmox que falta se **identifica y se documenta**, no se esquiva.
- Dependencias solo con `scripts/update-lock.sh` (y `pnpm` con lockfile). No se edita a mano `backend/requirements.lock`.
- Secretos: nunca impresos, registrados ni commiteados. Antes de cada commit:
  `gitleaks detect --no-banner --redact --config .gitleaks.toml`.
- Nunca `.planning/*` a mano, ni `--no-verify`, ni reescribir historia publicada, ni empujar a `master`.
- Nunca crear etiquetas `v*`, releases ni tocar claves de firma. Nunca ejecutar `install.sh` fuera del laboratorio.
- Los agentes no hacen commits, ramas, PR ni usan `gh`/`git` de escritura: lo hace el orquestador (commits atómicos por tarea,
  un PR por fase hacia `master`, que fusiona el humano).

## Cuándo parar

Se para y se deja la pregunta por escrito en el PR cuando: un cambio requiera relajar un control; haya que decidir algo
de producto; una prueba de laboratorio dé un resultado distinto al esperado y no sea evidente por qué; o falten
credenciales o acceso. Las paradas también se avisan al celular si el coordinador lo permite.

## Formato de una decisión

Una línea en `docs/hardening/DECISIONS.md`: `| Xnn | decisión | motivo |`, con `PROVISIONAL` o `DECISIÓN HUMANA` y su
impacto (*operación* o *forma interna*) dentro de la decisión. Las decisiones humanas ya tomadas no se reabren.
