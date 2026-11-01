# Instrucciones para OpenCode (implementador en claude2)

Este documento complementa a `V0.1.1-PLAN.md` (qué hacer) y a `CLAUDE.md` (reglas del proyecto). Para trabajo autónomo
con acceso al laboratorio, **prevalece sobre `AUTONOMY.md`**, que se escribió para una máquina desechable sin acceso a
infraestructura.

## 1. Antes de empezar: aislar al agente

claude2 **no es una máquina desechable**. Un agente autónomo escribe código y ejecuta comandos, así que el aislamiento
es la barrera contra un error o contra contenido malicioso dentro del repositorio o de una dependencia.

- Ejecuta OpenCode con el usuario sin privilegios (`dakser`), nunca como root y sin `sudo` sin contraseña.
- Que ese usuario **no tenga** claves SSH hacia nodos Proxmox de producción ni tokens de otros servicios. Usa un
  directorio de trabajo propio (`~/work/proxmox-gui`), no tu carpeta personal ni la de otros proyectos.
- Las credenciales del laboratorio van solo en `~/.pgui-lab.env` (modo `0600`, fuera del repositorio):

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

**Nivel 1 — pila de desarrollo en claude2 contra `prox-t1` (el agente lo hace solo).**
Backend y frontend corren en claude2 con una base SQLite y un `master.key` temporales (en un directorio efímero), y el
clúster del laboratorio se registra con el token del agente. Sirve para L-01 a L-09, L-12, L-14 y casi todo M-xx.
Cada comprobación deja evidencia (petición, código de estado, objeto creado en Proxmox) en `docs/hardening/LAB-RESULTS.md`,
**sin secretos**. Al terminar una prueba, el agente limpia lo que creó en Proxmox (pools, usuarios `gui-team-*`, CT, VM).

**Nivel 2 — release candidate en el LXC 200 (lo ejecuta Roberto).**
Lo que depende de systemd, del instalador o del contenedor real (L-10, L-11, L-13, L-15, M-05) no se puede dar por bueno
en claude2. El agente deja un guion corto y comentado en `docs/hardening/LAB-RC.md` con los comandos exactos, y Roberto
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
  permisos separados, directivas de `deploy/systemd/*`, CSP del `Caddyfile.template`, política de destinos
  (`target_policy.py`), límite de intentos del setup, privilegios separados de los tokens por equipo.
- Editar `.planning/*` a mano; usar `--no-verify`; reescribir historia publicada; empujar a `master`.
- Crear etiquetas `v*`, releases o tocar claves de firma. Ejecutar `install.sh` en claude2.
- Añadir una cuenta con privilegios amplios en Proxmox ("super-token") para esquivar un permiso que falta: el permiso
  que falta se **identifica y se documenta** (L-05), no se esquiva.

## 6. Cuándo parar y preguntar

Parar y dejar la pregunta por escrito en el PR cuando: un cambio requiera relajar un control (sección 5); haya que decidir
algo de producto (L-17, cómo se concede el almacenamiento en M-06, el modelo de M-05); una prueba de laboratorio
dé un resultado distinto al esperado y no sea evidente por qué; o falten credenciales o acceso.

## 7. Dato pendiente para cerrar L-04

Roberto debe ejecutar esto en `prox-t1` (no imprime el secreto y borra el token):

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

> Lee `CLAUDE.md`, `docs/hardening/V0.1.1-PLAN.md` y `docs/hardening/BRIEF-OPENCODE.md`. Trabaja el plan de v0.1.1 en el
> orden de la sección 3 del plan, empezando por L-00. Un PR por punto, con prueba que falle primero. No toques secretos
> ni etiquetas `v*`; no debilites el endurecimiento. Deja la evidencia de laboratorio en `docs/hardening/LAB-RESULTS.md`
> sin secretos, y para en cada punto de la sección 6 del brief.
