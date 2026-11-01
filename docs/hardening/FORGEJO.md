# Forja propia (Forgejo + runner): código, PR y CI sin GitHub (decisión X28)

Qué reemplaza: el repositorio, los PR y el CI que estaban en GitHub. El producto ya no descarga nada de GitHub (L-18); esto
completa la independencia con la forja. **No cubre** lo que sigue siendo externo por naturaleza: los registros de paquetes
(PyPI, npm, apt), las descargas del toolchain fijadas por hash (Node, uv, gitleaks; `deploy/pins.env` y `setup-lxc.sh`), los
modelos de IA y el catálogo opcional de community-scripts.

## Piezas

| Pieza | Dónde | Notas |
|---|---|---|
| Forgejo (servidor Git, PR, Actions) | CT nuevo `pgui-forge` (Debian 12 o Ubuntu 22.04) | HTTPS (Caddy o el proxy que ya uses), registro público **desactivado**. |
| Runner (`forgejo-runner`) | el mismo CT, usuario `runner`, etiqueta `pgui` en **modo host** | Ejecuta los workflows de `.forgejo/workflows/` directamente en el CT. Es ejecución remota de código: solo para este repo, y el CT no guarda nada más. |
| Orquestador y agentes | CT `pmx-gui-lab` | Solo hablan con la forja por HTTPS con `FORGEJO_TOKEN` (un token por repositorio, sin permisos de administración). |

El runner está en un CT distinto al de los agentes a propósito: el CI no comparte sistema de archivos ni credenciales con ellos.

## 1. Servidor

Instala Forgejo con la guía oficial de instalación desde binario (verifica la firma GPG que publican) o su paquete, y deja:

```ini
[server]
ROOT_URL = https://<tu-forja>/
[service]
DISABLE_REGISTRATION = true
[actions]
ENABLED = true
```

Crea tu usuario administrador por la línea de comandos de Forgejo y activa 2FA. Crea un usuario `pgui-orch` (no admin) que sea
colaborador con escritura **solo** del repositorio; su token (Ajustes → Aplicaciones) con permisos `write:repository` y
`write:issue` (los PR cuentan como issue) es el `FORGEJO_TOKEN`.

## 2. Traer el repositorio

Nueva migración → GitHub → `https://github.com/dakser/proxmox-gui`, sin marcar «mirror». Trae las ramas (`master`,
`docs/kit-v0.1.1`) y el tag `v0.1.0`. Los PR e issues de GitHub no hacen falta. El repo público de GitHub no se toca: cuando
quieras, lo archivas, lo haces privado o lo borras tú.

## 3. Runner (modo host)

1. Descarga `forgejo-runner` desde `code.forgejo.org/forgejo/runner/releases` y **verifica su firma GPG** (la clave y el
   comando están en la guía oficial del runner). Instálalo en `/usr/local/bin`.
2. `useradd --create-home runner`; `su - runner -c 'forgejo-runner generate-config > runner-config.yml'`.
3. Token de registro: Forgejo → Administración del sitio → Actions → Runners → Crear token (o el del repositorio).
   `forgejo-runner register --no-interactive --instance https://<tu-forja> --token <TOKEN> --name pgui-runner --labels pgui:host`
4. Unidad systemd `forgejo-runner.service` (la de `contrib/` del repositorio del runner) y `systemctl enable --now`.
5. Herramientas que los workflows suponen instaladas en el CT, como el usuario `runner` (usa `orchestrator/setup-lxc.sh` como
   referencia de versiones y sumas): Node 22 con `corepack`, `uv` (Python 3.12), `shellcheck`, `gitleaks`, `perl`, `sqlite3`,
   `openssh-client`, `git`, `curl`.
6. Para el job `smoke-systemd`: Docker en ese CT, que exige **nesting y keyctl** en el CT de la forja. Si no lo quieres, borra ese
   job del workflow en un commit `KIT:` y que el nivel 2 cubra esa prueba.

Los workflows no usan acciones de terceros: hacen el checkout con `git` y el token automático del job.

## 4. Lo que el orquestador necesita

En `orchestrator/orchestrator.toml`: `forge_url = "https://<tu-forja>"` y `forge_repo = "<dueño>/proxmox-gui"`. En
`/etc/pgui-orch/secrets.env`: `FORGEJO_TOKEN=…`. El orquestador usa la API de Forgejo (PR en borrador con prefijo `WIP: `, estado
del CI por commit) y empuja con el token en variables de entorno de git, nunca en la línea de comandos.

## Límites conocidos

- Dependabot no existe en Forgejo: las actualizaciones de dependencias y pines las haces con `scripts/update-lock.sh` y
  `scripts/update-pins.sh` (o instalas Renovate aparte, si lo quieres).
- La API de Forgejo no ofrece el registro de un job fallido con un token de API: el orquestador entrega al implementador el
  nombre y el enlace de cada comprobación en rojo, más la salida local de la puerta de la fase.
- Nada de esto se ha probado contra un Forgejo real (la autoprueba usa un servidor falso): el primer `once F0.1` y el primer
  push al CI lo comprobarán; si algo falla, pega la salida.
