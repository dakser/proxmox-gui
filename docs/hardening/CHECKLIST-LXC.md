# Checklist: poner en marcha el modo autónomo (una página)

Tiempo estimado: ~45 min. Marca cada paso. Nada de lo que sigue necesita `pre-pgui`: el LXC dedicado vive **fuera** de `prox-t1`.

## A. Ten a mano (antes de empezar)

| # | Valor | De dónde sale |
|---|---|---|
| 1 | Token de tu **Forgejo** (usuario `pgui-orch`, colaborador solo de ese repo; `write:repository` + `write:issue`). Ver `docs/hardening/FORGEJO.md` | Forgejo → Ajustes → Aplicaciones |
| 2 | Clave de **OpenRouter** propia del proyecto, con tope de gasto mensual (el orquestador corta en 250 USD en total) | openrouter.ai → Keys |
| 3 | Sesión de **OpenCode Go** (cuota compartida, ~68 % usada, renueva el 15-nov: revísala cada semana) | `opencode auth login` |
| 4 | Sesión de **Claude Code** (suscripción, para Opus) | `claude` → `/login` |
| 5 | Tema privado de avisos (p. ej. ntfy con un nombre largo y aleatorio) o nada | tú; el mensaje solo lleva estado, nunca secretos |
| 6 | Contraseña larga para el admin del **stack de desarrollo** (no la del LXC 200) | tú |

## B. Pasos

- [ ] **0. La forja** (`docs/hardening/FORGEJO.md`): CT `pgui-forge` con Forgejo + runner (etiqueta `pgui`, modo host), migración desde
  `github.com/dakser/proxmox-gui`, y fusión en Forgejo de `docs/kit-v0.1.1` en `master` (PR nuevo en Forgejo). Sin eso el LXC
  no tiene el orquestador en `master`. Rellena `forge_url` y `forge_repo` en `orchestrator/orchestrator.toml` (commit `KIT:`).
- [ ] **1. El CT** (`pmx-gui-lab`, Ubuntu 22.04, ya creado). Comprueba: **sin privilegios**, **sin nesting ni keyctl**, ≥4 vCPU / 8 GB / 40 GB, red que alcance `192.168.100.156:8006` y `192.168.100.160`, y que esté **fuera** de `prox-t1`. Toma un snapshot a mano antes de seguir (lo tomarás tú; el orquestador no tiene token).
- [ ] **2. Token de laboratorio en `prox-t1`** (los permisos efectivos son la intersección: al **usuario y al token**):
  ```
  pveum user add pgui-lab-agent@pve
  pveum acl modify / --users pgui-lab-agent@pve --roles PortalAdmin
  pveum user token add pgui-lab-agent@pve dev --privsep 1      # copia el secreto; no se vuelve a mostrar
  pveum acl modify / --tokens 'pgui-lab-agent@pve!dev' --roles PortalAdmin
  ```
  **No** le des `SDN.Use`: F2 debe descubrirlo y documentarlo desde cero (R-15).
- [ ] **3. En el CT como root:** `apt-get update && apt-get install -y git curl`, luego
  `git clone https://<tu-forja>/<dueño>/proxmox-gui /root/pgui && /root/pgui/orchestrator/setup-lxc.sh` (con el token 1 si el repo es privado: `git -c http.extraHeader="Authorization: token …" clone …`).
  Verifica en la salida que las sumas de Node y gitleaks pasan; las versiones fijadas son de referencia, confírmalas.
- [ ] **4. Como `agent`** (`su - agent`):
  `git clone https://<tu-forja>/<dueño>/proxmox-gui ~/proxmox-gui && cd ~/proxmox-gui` (el token en un `credential.helper store` de `agent`, modo 0600), y los dos
  `install` que imprime `setup-lxc.sh` (venv de Python 3.12 con `requirements.lock` + pytest/ruff/mypy/pip-audit/bandit, y
  `pnpm install --frozen-lockfile`). Pon `~/proxmox-gui/backend/.venv/bin` en el `PATH` del usuario.
- [ ] **5. Credenciales** (todas en `agent`, modo 0600, fuera del repo):
  `~/.pgui-lab.env` con `PGUI_LAB_PVE_URL=https://192.168.100.156:8006`, `PGUI_LAB_PVE_TOKEN_ID=pgui-lab-agent@pve!dev`,
  `PGUI_LAB_PVE_TOKEN_SECRET=…`, `PGUI_LAB_PORTAL_URL=https://192.168.100.160`, `PGUI_LAB_ADMIN_USER=admin`,
  `PGUI_LAB_ADMIN_PASSWORD=…` (valor 6).
  Como root: `/etc/pgui-orch/secrets.env` (`root:agent`, 0640) con `FORGEJO_TOKEN=…` (valor 1) y `OPENROUTER_API_KEY=…` (valor 2).
  En el `.profile` de `agent`: `export OPENROUTER_API_KEY=…`. Luego `opencode auth login` y `claude` → `/login`.
- [ ] **6. Avisos** (opcional): en `orchestrator/orchestrator.toml`, `[notify] command = "curl -fsS -d \"$PGUI_MSG\" https://ntfy.sh/<tema>"`
  (cambio `KIT:` en una rama o a mano en el LXC; no lo toca ningún agente). `[snapshot] command` se queda **vacío**.
- [ ] **7. Verificación (cada paso debe salir bien):**
  ```
  python3 orchestrator/pgui_orch.py init      # "Kit verificado."
  python3 orchestrator/pgui_orch.py seal      # anota la huella
  python3 orchestrator/pgui_orch.py seal      # debe decir "(igual a la anterior)"
  python3 orchestrator/pgui_orch.py dry-run   # F0 → F4, sin llamar a ningún agente
  ```
- [ ] **8. Primera corrida real, una sola tarea:** `python3 orchestrator/pgui_orch.py once F0.1`, luego `status` y el diff en la
  rama `fase/F0`. Aquí se afinan las partes aún sin probar con agentes reales (clasificación de errores, banderas de `claude -p`,
  versiones). Si algo falla, pégame la salida; no sigas.
- [ ] **9. Soltarlo:** `tmux new -s pgui` → `python3 orchestrator/pgui_orch.py run`. Desacopla con `Ctrl-b d`. Relanzar `run` retoma.
- [ ] **10. Mientras corre:** mira el PR `WIP:` de `integracion/v0.1.1` y el CI en Forgejo; cada semana, la cuota de OpenCode Go.
  Para parar: `Ctrl-C` en la sesión de tmux.
- [ ] **11. Al final** (el aviso dice cuándo): nivel 2 consolidado con `docs/hardening/LAB-RC.md` en `prox-t1` (snapshot antes),
  `reports/lab2/<fase>.json` en un commit `KIT: nivel 2`, **tú firmas** la release con `scripts/release-sign.sh`, fusionas el PR.

## C. Limpieza al terminar
Borrar el token `pgui-lab-agent@pve!dev` y el usuario; revocar el token de la forja; revocar la clave de OpenRouter; destruir el CT.

## D. Cuándo te avisa o se detiene
Tarea bloqueada tras sus intentos · presupuesto alcanzado · revisor que rechaza tras 2 rondas · límite del proveedor · CI que no
se corrige en 3 rondas. Todo queda en `orchestrator/logs/` y `docs/BLOCKERS.md`.
