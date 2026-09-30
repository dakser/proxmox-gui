# Protocolo cliente ↔ gate SSH (F-01, F-11, D1/D8)

Canal opcional (`install.sh --enable-community-scripts`, apagado por defecto) entre el LXC de la GUI y el nodo PVE. Existe
solo para que el worker ejecute `pct exec` dentro de contenedores que la propia app creó (community-scripts) y para el preflight.

## Lado host

`/root/.ssh/authorized_keys` del nodo, una línea por LXC de la GUI:

```
restrict,from="<IP del LXC>",command="/usr/local/sbin/proxmox-gui-ssh-gate" ssh-ed25519 AAAA… proxmox-gui@<ctid>
```

- `restrict` desactiva pty, reenvíos (agente, X11, puertos) y `~/.ssh/rc`.
- `command=` fuerza el gate: el cliente **no** elige el programa; solo aporta `SSH_ORIGINAL_COMMAND`.
- El gate es `root:root 0755`, sin shell (`exec` con lista de argumentos), Perl + `JSON::PP` (ambos de PVE base).

## Comandos (`SSH_ORIGINAL_COMMAND`)

| Comando | Efecto |
|---|---|
| `preflight` | Ejecuta únicamente `pct list` (salida descartada). Imprime `PREFLIGHT_OK` y sale 0 si `pct list` fue 0; si no, sale 1. |
| `exec <vmid>` | Ejecuta `pct exec <vmid> -- [env K=V…] <argv…>` (ver abajo). |

Cualquier otro texto, o argumentos extra, se rechazan (exit 126, mensaje en stderr).

### `exec <vmid>` — stdin

Primera línea de stdin: JSON en una sola línea terminada en `\n`

```json
{"env": {"CTID": "201", "VERBOSE": "yes"}, "argv": ["bash", "-c", "yes y | bash -c \"$(curl -fsSL https://…)\""]}
```

El resto de stdin se entrega **tal cual** al proceso dentro del CT (p. ej. `"y\n" * 50` para whiptail). El gate lee la primera
línea byte a byte (sin buffering) para no consumir el resto.

Para preservar el comportamiento actual (`provisioning_functions.py::_build_install_env`/`_build_install_command`,
`connector.lxc_exec`): las variables se entregan **dentro del CT** con `env K=V… -- argv…` (nunca al proceso `pct` del host: esto
evita `PERL5OPT`/`LD_PRELOAD` contra un `pct` que corre como root), y `argv` se pasa como lista, sin `sh -c` del lado del host.

## Validaciones del gate (todas fallan cerrado; exit 126 salvo indicación)

1. `vmid` cumple `^[1-9][0-9]{2,8}$`.
2. `pct config <vmid>` funciona (el CT existe **en este nodo**), contiene `unprivileged: 1` y el tag `proxmox-gui` (D8). Los tags
   admiten separadores `;`, `,` o espacio.
3. JSON ≤ 65 536 bytes, objeto con solo `env` (objeto, opcional) y `argv` (lista no vacía de cadenas).
4. `argv`: ≤ 32 elementos, cada uno ≤ 16 384 bytes, sin NUL; `argv[0]` no empieza por `-` ni contiene `=`.
5. `env`: ≤ 64 entradas; nombre `^[A-Za-z_][A-Za-z0-9_]*$` (la lista real incluye `app` y `tz` en minúscula, que exige
   community-scripts; desviación consciente de la regex solo-mayúsculas del plan) y **no** `LD_*`, `BASH_*`, `PERL*`, `PYTHON*`,
   `ENV`, `PATH`, `IFS`, `SHELLOPTS`, `BASHOPTS`, `PS4`, `CDPATH`, `GLOBIGNORE`, `HOME`, `SHELL`; valor ≤ 4 096 bytes, sin NUL.
6. Tiempo máximo de `exec`: 3 600 s (el mismo tope que `lxc_exec`); al vencer, se mata el grupo de procesos (exit 124).

## Registro y códigos de salida

Cada llamada se registra con `logger -t proxmox-gui-gate`: comando, vmid, SHA-256 del argv (nunca el contenido ni el `env`),
código de salida o motivo del rechazo. Salida: código del proceso dentro del CT; 124 timeout; 126 rechazo del gate.

## Cliente (backend)

`ssh -i <clave> -o IdentitiesOnly=yes -o UserKnownHostsFile=<known_hosts> -o StrictHostKeyChecking=yes -o BatchMode=yes
-o ClearAllForwardings=yes -T root@<node> exec <vmid>` con el JSON + stdin por stdin; `… preflight` para el preflight. `node` se
valida contra `^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$` antes de lanzar el proceso.

## Registro y datos externos (F-16)

Todo dato que viene del cliente (nombres de variables, mensajes de rechazo) se reduce a ASCII imprimible y se acota a 120 caracteres
antes de escribirse en syslog o en stderr: no hay inyección de líneas ni secuencias de escape. Al vencer el tiempo máximo se envía `TERM` al grupo
de procesos y, pasados 5 s, `KILL`.

## Límites conocidos

- **La tag `proxmox-gui` es la única marca de propiedad** (D8) y cualquier administrador de PVE puede ponerla a cualquier CT sin privilegios del
  nodo: el gate ejecutará entonces `pct exec` en él para quien controle la app. Riesgo residual aceptado; mitigación operativa: restringir quién
  puede editar tags en PVE y revisar `pct list` periódicamente.

- `from=` depende de la IP del LXC: con DHCP puede cambiar; usar `--ip/--gw` (IP estática) en la instalación.
- `pct exec` en un CT sin privilegios queda acotado al contenedor, pero el CT con tag es de la GUI: quien comprometa la app puede
  ejecutar código en esos CT (riesgo residual documentado, D1).
