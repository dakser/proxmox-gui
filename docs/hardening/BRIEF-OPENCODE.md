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

**Reparto de roles** (esquema de proyectos autónomos): OpenCode implementa y escribe las pruebas de aceptación (el
implementador no toca `tests/acceptance/` ni `lab/scenarios/` una vez sellados); otro modelo revisa el diff contra
`docs/spec/v0.1.1.md`; **Claude Code** hace la revisión de seguridad de las fases con más superficie (F1, F2 y F3). El
coordinador (el programa que reparte tareas, corre `gates.toml` y avisa) es el de fusiond, adaptado; aún no está en este
repositorio.

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

## 4. Flujo por punto del plan

1. Rama `fix/L-xx-descripcion` o `feat/M-xx-descripcion` desde `master`.
2. **Primero una prueba que falle** y reproduzca lo observado (para L-01: cookie de acceso caducada con refresh vigente;
   para L-04: conector simulado que devuelve error; etc.).
3. Implementar el cambio mínimo. Si toca la superficie de la API, actualizar lo que dependa de ella (esquemas, tipos
   del frontend en `frontend/src/lib/api/`, y `backend/tests/test_http_surface.py`).
4. `scripts/check.sh` completo, `pytest -q` en `backend/` y, en `frontend/`, `pnpm check && pnpm test && pnpm build`.
5. Verificación de nivel 1 si aplica; anotar el resultado en `LAB-RESULTS.md`.
6. Commits atómicos con mensajes convencionales (`fix(frontend): ...`). Un PR por punto hacia `master`, con: qué se vio,
   causa, qué cambia, cómo se probó (nivel 1/2), y el riesgo para el endurecimiento.
7. El PR debe pasar **todo** el CI (`shell`, `backend`, `frontend`, `scans`, `smoke-systemd`) antes de pedir revisión.
   Empieza por **L-00**: con `scans` en rojo no se fusiona nada.
8. Dependencias solo con `scripts/update-lock.sh` (y `pnpm` con lockfile); nunca editar a mano `requirements.lock`.

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

**Resultado (2026-11-01):** con `PortalAdmin` el token recibe `{"data":[]}`: Proxmox filtra la lista. Hipótesis a confirmar:
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

> Lee `AGENTS.md`, `CLAUDE.md`, `docs/spec/v0.1.1.md`, `docs/plan.md`, `gates.toml` y `docs/hardening/BRIEF-OPENCODE.md`.
> Trabaja el plan por fases, empezando por F0. Un PR por punto, con la prueba de aceptación escrita antes que el código y su
> mutación anotada en `docs/BLOCKERS.md`. No toques secretos ni etiquetas `v*`; no debilites el endurecimiento. Deja la
> evidencia de laboratorio en `docs/hardening/LAB-RESULTS.md` sin secretos, y para en cada punto de la sección 6.
