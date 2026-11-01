# Instrucciones para OpenCode (implementador en un LXC dedicado)

Este documento complementa a `V0.1.1-PLAN.md` (qué hacer) y a `CLAUDE.md` (reglas del proyecto). Para trabajo autónomo
con acceso al laboratorio, **prevalece sobre `AUTONOMY.md`**, que se escribió para una máquina desechable sin acceso a
infraestructura.

## 1. Antes de empezar: aislar al agente

Un agente autónomo escribe código y ejecuta comandos, así que el aislamiento es la barrera contra un error o contra
contenido malicioso dentro del repositorio o de una dependencia. **No se ejecuta en una máquina que ya tenga otros
servicios** (se descartó claude2 por eso). Se usa un **LXC desechable y dedicado** a este proyecto:

- **Sin privilegios** (`unprivileged`), sin `nesting`, sin `keyctl`, sin puntos de montaje del host ni dispositivos.
  4 vCPU, 8 GB de RAM y 40 GB de disco bastan. Debian 12 con Python 3.12, Node 22, `pnpm`, `uv`, `shellcheck`, `sqlite3`,
  `gitleaks`, `openssh-client` y `git`; OpenCode y Claude Code, instalados para un usuario **sin privilegios**
  (`agent`), sin `sudo`.
- **Instantánea antes de cada fase** (`pct snapshot`), y aviso en cada parada. Lo que se pierde al revertir es solo el
  entorno: el trabajo vive en el fork de GitHub (ramas y PR), así que se empuja a menudo.
- **Sin** claves SSH hacia nodos Proxmox, ni tokens de otros servicios, ni la clave privada de firma de releases.
  Acceso a GitHub con un token de grano fino limitado al fork (`Contents` y `Pull requests`, sin `Workflows`).
- Salida a internet solo hacia lo necesario: GitHub, PyPI, npm, `nodejs.org` y los proveedores de modelos. Si quieres
  endurecer más, el firewall del propio Proxmox puede limitar el tráfico de ese CT hacia tu red (permitir solo el
  `192.168.100.156:8006` del laboratorio y el `192.168.100.160` del portal).
- El LXC dedicado vive **fuera** de `prox-t1` (decisión de Roberto, 2026-11-01): restaurar los snapshots del laboratorio
  anidado no lo toca. Debe alcanzar `192.168.100.156:8006` y `192.168.100.160` por red; el agente no tiene otra vía hacia
  el laboratorio.
- Las credenciales del laboratorio van solo en `~agent/.pgui-lab.env` (modo `0600`, fuera del repositorio):

```
PGUI_LAB_PVE_URL=https://192.168.100.156:8006
PGUI_LAB_PVE_TOKEN_ID=pgui-lab-agent@pve!dev
PGUI_LAB_PVE_TOKEN_SECRET=...
PGUI_LAB_PORTAL_URL=https://192.168.100.160
PGUI_LAB_ADMIN_USER=admin
PGUI_LAB_ADMIN_PASSWORD=...
```

  Crea un token **propio para el agente** (no el del portal, no root@pam) con el rol `PortalAdmin` documentado en L-05
  y bórralo al terminar. El laboratorio es el PVE anidado `prox-t1`; el agente **no toca** el hipervisor exterior ni el
  snapshot `pre-pgui`.
- Nunca imprimir, registrar, commitear ni pegar en un PR o issue ningún secreto. Antes de cada commit:
  `gitleaks detect --no-banner --redact --config .gitleaks.toml`.
- Sin acceso `pct exec` ni SSH root al nodo para el agente. Lo que haga falta ejecutar ahí lo prepara el agente y lo
  ejecuta Roberto (ver nivel 2).

**Reparto de roles** (esquema de proyectos autónomos, ver `docs/hardening/ORCHESTRATOR.md`): el **autor de pruebas** (Claude Haiku
vía OpenRouter) escribe las pruebas de aceptación y los escenarios de laboratorio antes del código; el **implementador**
(DeepSeek V4.1 Flash vía OpenCode Go) hace que pasen; el **revisor** (MiMo-V2.5 vía OpenCode Go) juzga el diff contra
`docs/spec/v0.1.1.md`; y el **revisor de seguridad** (Claude Opus por Claude Code) revisa las fases con superficie de ataque
(F1, F2, F3). Ninguno hace commits, ramas, PR ni toca `gh`: lo hace el **orquestador** (`orchestrator/pgui_orch.py`, sin LLM), que
también corre las puertas de `gates.toml`. Las guardas (`.claude/hooks/guard.py` y el plugin de OpenCode) bloquean escrituras
fuera del rol.

## 2. Hechos del laboratorio (no son secretos)

| | |
|---|---|
| Nodo Proxmox | `prox-t1`, PVE 9.2.2, `https://192.168.100.156:8006`, huella SHA-256 en la ficha del clúster del portal |
| Portal | LXC 200 (`proxmox-gui`), `https://192.168.100.160`, release `v0.1.0`, certificado autofirmado de Caddy |
| Almacenamiento | `local` (dir, con la plantilla `debian-12-standard_12.12-1_amd64.tar.zst`), `local-lvm` (lvmthin) |
| Redes | `vmbr0` (sin SDN: `This cluster is not SDN-capable`) |
| Usuarios del portal | `admin`; `lab1` (miembro de `lab-team`); otro usuario creado antes (`personal-2`) |
| Pools en PVE | `gui-team-1`, `gui-team-2` (equipos personales provisionados al registrar), `gui-team-4` (`lab-team`) |
| Ajuste solo de laboratorio | drop-in en `proxmox-gui-api`: `PROXMOX_GUI_ACCESS_TOKEN_TTL_SECONDS=3600`. **No** forma parte de ninguna release |

## 3. Dos niveles de verificación

**Nivel 1 — pila de desarrollo en el LXC dedicado contra `prox-t1` (el agente lo hace solo).**
Backend y frontend corren en el LXC dedicado con una base SQLite y un `master.key` temporales (en un directorio efímero), y el
clúster del laboratorio se registra con el token del agente. Sirve para L-01 a L-09, L-12, L-14 y casi todo M-xx.
Cada comprobación deja evidencia (petición, código de estado, objeto creado en Proxmox) en `docs/hardening/LAB-RESULTS.md`,
**sin secretos**. Al terminar una prueba, el agente limpia lo que creó en Proxmox (pools, usuarios `gui-team-*`, CT, VM).

**Nivel 2 — release candidate en el LXC 200 (lo ejecuta Roberto).**
Lo que depende de systemd, del instalador o del contenedor real (L-10, L-11, L-13, L-15, M-05) no se puede dar por bueno
en el LXC dedicado. El agente deja un guion corto y comentado en `docs/hardening/LAB-RC.md` con los comandos exactos, y Roberto
los ejecuta en `prox-t1` tras un snapshot, y devuelve la salida. Las releases `v0.1.1-rcN` son **prereleases** que firma
Roberto con `scripts/release-sign.sh`; el agente nunca ve la clave privada ni crea etiquetas `v*`.

## 4. Flujo (lo ejecuta el orquestador; esto es lo que ve cada agente)

1. El orquestador crea la rama `fase/Fx` desde `master` y reparte las tareas de `orchestrator/tasks.toml` en orden.
2. Por cada tarea con pruebas: primero el **autor de pruebas** escribe lo que el manifiesto `tests/acceptance/manifest.toml`
   asigna a esa tarea (nombre y archivo exactos) y el orquestador lo sella en un commit `T Fx.y`; luego el **implementador**
   hace el cambio mínimo hasta que pasen `make check` y esas pruebas, aplica la mutación de cada una y la anota como
   `MUTACIÓN <prueba>: detectada` en `docs/BLOCKERS.md`; el orquestador lo verifica y hace el commit `Fx.y: <título>`.
3. Si el implementador cree que la prueba es la errónea: línea `CULPA: prueba` y registro PROVISIONAL en `DECISIONS.md`;
   nunca la edita.
4. Cuando acaban las tareas: puerta rápida (`gate.py Fx --quick`), revisión (revisor y, en F1 a F3, revisor de seguridad) y puerta
   completa (`full`, escenarios de laboratorio de nivel 1 con `lab/run.sh`, métricas). Si algo falla, rondas de corrección.
5. `lock-check` (ninguna mezcla de pruebas y código, nada del kit tocado) y publicación: el orquestador empuja la rama y
   abre **un PR por fase** hacia `master` (un commit por tarea). **Se detiene hasta que Roberto lo fusione** con el CI completo
   en verde; al relanzar, sigue con la fase siguiente. Nadie fusiona solo.
6. Dependencias solo con `scripts/update-lock.sh` (y `pnpm` con lockfile); nunca editar a mano `requirements.lock`.

## 5. Prohibido

- Debilitar un control del endurecimiento: firma de releases (`deploy/release-signers`, `install.sh`), usuarios y
  permisos separados, directivas de `deploy/systemd/*`, CSP de `deploy/caddy/Caddyfile.template`, política de destinos
  (`target_policy.py`), límite de intentos del setup, privilegios separados de los tokens por equipo.
- Editar `.planning/*` a mano; usar `--no-verify`; reescribir historia publicada; empujar a `master`.
- Crear etiquetas `v*`, releases o tocar claves de firma. Ejecutar `install.sh` fuera del laboratorio.
- Añadir una cuenta con privilegios amplios en Proxmox ("super-token") para esquivar un permiso que falta: el permiso
  que falta se **identifica y se documenta** (L-05), no se esquiva.

## 6. Cuándo parar y preguntar

Parar y dejar la pregunta por escrito en el PR cuando: un cambio requiera relajar un control (sección 5); haya que decidir
algo de producto (L-17, cómo se concede el almacenamiento en M-06, el modelo de M-05); una prueba de laboratorio
dé un resultado distinto al esperado y no sea evidente por qué; o falten credenciales o acceso.

## 7. Dato pendiente para cerrar L-04

**Resultado (2026-11-01):** con `PortalAdmin` el token recibe `{"data":[]}`: Proxmox filtra la lista. **Confirmado** (con `PVESDNUser` en `/sdn/zones/localnetwork` para usuario y token aparece `vmbr0`; solo para el token, no). Lo que sigue es el guion usado. Hipótesis original:
falta `SDN.Use` sobre `/sdn/zones/localnetwork` (desde PVE 8.1, sin `Sys.Modify` solo se ven los bridges con `SDN.Use`). Si se
confirma, los tokens de equipo también lo necesitan para conectar CT/VM a `vmbr0`; el diseño decide quién lo concede (admin,
por red y equipo). Comprobación:

```bash
SECRET=$(pveum user token add pgui-admin@pve diag --privsep 1 --output-format json | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')
pveum acl modify / --tokens 'pgui-admin@pve!diag' --roles PortalAdmin
curl -ks -H "Authorization: PVEAPIToken=pgui-admin@pve!diag=$SECRET" \
  "https://192.168.100.156:8006/api2/json/nodes/prox-t1/network?type=any_bridge" | head -c 400; echo
pveum user token remove pgui-admin@pve diag; unset SECRET
```

Si devuelve `{"data":[]}`, Proxmox filtra la lista para ese token (probable falta de `SDN.Use`/`SDN.Audit`). Si devuelve
`vmbr0`, el fallo está en el lado del portal. Mientras tanto, el agente puede avanzar con L-00 y L-01.

## 8. Prompt de arranque

Ya no se pega un prompt: el orquestador compone el de cada sesión (tarea, reglas `R-xx`, sección del plan, pruebas del
manifiesto) y OpenCode carga `AGENTS.md` solo. Para arrancar: `docs/hardening/ORCHESTRATOR.md`.
