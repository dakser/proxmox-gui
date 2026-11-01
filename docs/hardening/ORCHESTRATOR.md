# Cómo se usa el orquestador (modo autónomo)

El orquestador (`orchestrator/pgui_orch.py`, Python estándar, sin LLM) reparte las tareas de `orchestrator/tasks.toml`, lanza a
los agentes con su rol, verifica su trabajo, hace los commits y corre las puertas de `gates.toml`. Es material **humano**: solo
se cambia con commits `KIT: …`. Reglas del trabajo: `AGENTS.md`. Qué hacer: `docs/spec/v0.1.1.md` y `docs/plan.md`.

## Qué llena el humano (todo lo demás ya está)

En el LXC dedicado, como usuario `agent`, tras `orchestrator/setup-lxc.sh`:

| Qué | Dónde | Notas |
|---|---|---|
| Aviso al celular | `[notify] command` de `orchestrator/orchestrator.toml` | Se ejecuta con el mensaje en `$PGUI_MSG` (nunca interpolado). Vacío = solo registro en `orchestrator/logs/`. |
| Snapshot antes de cada fase | `[snapshot] command` | **Vacío (decisión X25)**: no se da ningún token de Proxmox al LXC. Si quieres puntos de restauración, toma tú el snapshot desde Proxmox, o programa un backup del CT en Proxmox. El código está a salvo porque se empuja tras cada commit. |
| Secretos del orquestador | `/etc/pgui-orch/secrets.env` (`root:agent`, 0640) | `KEY=VALUE`. Llega solo a `[notify]`, `[snapshot]` y a los comandos `git push`/`gh` del orquestador, nunca a `make check` ni a los agentes (que además tienen vetada la ruta). |
| Claves de modelos | `OPENROUTER_API_KEY` (clave propia del proyecto, con tope de gasto) en el entorno de `agent`; `opencode auth login`; `claude /login` | No van en ningún archivo del repo. |
| Credenciales de laboratorio | `~/.pgui-lab.env` (0600) | Formato en `BRIEF-OPENCODE.md` §1. Solo `lab/lib.sh` las lee. |
| GitHub | `gh auth login` con token de grano fino (solo el fork; Contents RW, Pull requests RW, Actions R, Metadata R; sin Workflows) | Sirve a `git push` y `gh pr create` del orquestador. |

Límite honesto: el usuario `agent` ejecuta tanto al orquestador como a los agentes, así que `/etc/pgui-orch/secrets.env` es
legible para el proceso de un agente. Las guardas lo vetan (heurística, no frontera). La frontera real son los **alcances** de
cada credencial: token de GitHub limitado al fork y sin `Workflows`, clave de OpenRouter con tope, token de laboratorio de Proxmox propio (`pgui-lab-agent@pve!dev`) y sin snapshots.

## Puesta en marcha

```
python3 orchestrator/pgui_orch.py init       # verifica el kit y corre las autopruebas (make selftest)
python3 orchestrator/pgui_orch.py seal       # sella la huella del material de aceptación; repetir: debe salir "igual a la anterior"
python3 orchestrator/pgui_orch.py dry-run    # muestra la secuencia de sesiones sin llamar a ningún agente
python3 orchestrator/pgui_orch.py run        # trabaja hasta cerrar una fase; publica su PR y se detiene
python3 orchestrator/pgui_orch.py status     # fases, tareas, intentos y gasto
python3 orchestrator/pgui_orch.py verify     # ¿cambió el material sellado fuera de un commit del autor de pruebas?
python3 orchestrator/pgui_orch.py once F1.2  # una sola tarea (depurar) ; reset F1.2 la devuelve a pendiente
```

Ciclo normal: `run` encadena todas las fases solo. Cada fase aprobada (puerta, revisión, `lock-check`) se fusiona en
`integracion/v0.1.1` —nunca en `master`—, se empuja y espera el CI de GitHub (si falla, el implementador lo corrige). El PR
`integracion/v0.1.1` → `master` nace en borrador y pasa a listo al terminar. Se detiene solo ante un bloqueo real; relanzar `run`
retoma donde quedó. Los pasos de nivel 2 (F3 y F4: instalador y actualización en el LXC 200) no detienen nada: quedan
anotados y, al final, el aviso lista lo que falta. Entonces ejecutas `docs/hardening/LAB-RC.md` de una vez, dejas
`reports/lab2/<fase>.json` con `{"pass": true, …}` en un commit `KIT: nivel 2`, firmas la release y fusionas.

## Qué comprueba cada pieza

- **Guardas por rol** (`.claude/hooks/guard.py`, `.opencode/plugins/pgui-guard.js`): escritura solo donde el rol puede; ni `git` ni
  `gh` de escritura; ni credenciales ni claves; kit y firma intocables. `scripts/guard_selftest.py` (48 casos).
- **Candado** (`seal`/`verify`, `ensure_locked`): la huella de `git ls-tree` del material de aceptación; si cambia fuera de un
  commit `T …`, el orquestador se detiene.
- **`lock_check.py`**: ningún commit mezcla pruebas de aceptación con código; nada del kit sin prefijo `KIT:`.
- **`gate.py`**: ejecuta `gates.toml`; un informe de laboratorio viejo no cuenta; una métrica ausente es un fallo
  (`scripts/gate_selftest.py`).
- **`accept_check.py`**: cada prueba del manifiesto existe con su nombre exacto; con `--mutations`, cada una tiene su línea
  `MUTACIÓN … : detectada`.
- **Laboratorio** (`lab/run.sh`, `lab/selftest.sh`): un escenario solo cuenta si sale con 0 y su informe trae `pass: true`.

## Sin probar todavía (se afina en el primer ensayo real del LXC)

Se ensayó con un agente falso (separación de commits, reversión de ediciones prohibidas, rechazo de revisor, candado, `lock-check`,
`push`). Todavía no se ha corrido con OpenCode ni con Claude Code reales ni con las dependencias del backend y el frontend:
la clasificación de errores de recolección de pruebas (`MISSING_API_RE` / `BROKEN_TEST_RE` en `pgui_orch.py`), las banderas de
`claude -p` (`--allowedTools`), el respaldo de proveedor (heredado sin cambios del coordinador de fusiond) y las versiones fijadas
en `setup-lxc.sh` (Node, gitleaks) hay que verificarlas en la primera corrida real, con `once F0.1`.
