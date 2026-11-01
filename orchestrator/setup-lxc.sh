#!/usr/bin/env bash
# shellcheck shell=bash
# Prepara el LXC dedicado y desechable del modo autónomo (Debian 12 o Ubuntu 22.04/24.04; sin privilegios, sin nesting ni keyctl).
# Uso (como root, una sola vez):  orchestrator/setup-lxc.sh
# Después, como el usuario `agent`:  claude /login   y   opencode auth login   (o las variables del proveedor en secrets.env).
# Qué NO hace: no crea claves SSH, no copia credenciales de Proxmox, no da sudo al usuario `agent`.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Ejecuta como root." >&2; exit 1; }

NODE_VERSION="${NODE_VERSION:-22.11.0}"          # LTS 22; ajusta si cambia la del proyecto (frontend/package.json "engines")
GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.21.2}"
AGENT_HOME=/home/agent

# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    debian:12|ubuntu:22.04|ubuntu:24.04) echo "== Sistema: ${PRETTY_NAME}" ;;
    *) echo "aviso: ${PRETTY_NAME:-desconocido} no está probado (Debian 12, Ubuntu 22.04/24.04); sigo bajo tu responsabilidad." >&2 ;;
esac

echo "== Paquetes del sistema"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    git curl ca-certificates jq xz-utils build-essential pkg-config perl sqlite3 openssh-client shellcheck \
    python3 python3-venv make gnupg locales tmux
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen >/dev/null

echo "== Usuario agent (sin privilegios, sin sudo)"
id agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent
passwd -l agent >/dev/null
install -d -m 0750 -o root -g agent /etc/pgui-orch
[[ -f /etc/pgui-orch/secrets.env ]] || install -m 0640 -o root -g agent /dev/null /etc/pgui-orch/secrets.env

echo "== Node ${NODE_VERSION} (con la suma de comprobación publicada por nodejs.org)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o "$tmp/node.tar.xz"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
(cd "$tmp" && grep " node-v${NODE_VERSION}-linux-x64.tar.xz\$" SHASUMS256.txt | sed 's# node-v.*# node.tar.xz#' | sha256sum -c -)
tar -xJf "$tmp/node.tar.xz" -C /usr/local --strip-components=1
corepack enable
echo "   (pnpm lo fija frontend/package.json -> packageManager; corepack lo descarga al primer uso)"

echo "== gitleaks ${GITLEAKS_VERSION}"
base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
curl -fsSL "$base/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" -o "$tmp/gl.tar.gz"
curl -fsSL "$base/gitleaks_${GITLEAKS_VERSION}_checksums.txt" -o "$tmp/gl.sums"
(cd "$tmp" && grep "gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz\$" gl.sums | sed 's#  .*#  gl.tar.gz#' | sha256sum -c -)
tar -xzf "$tmp/gl.tar.gz" -C /usr/local/bin gitleaks

echo "== Herramientas del usuario agent: uv (Python 3.12), Claude Code, OpenCode"
su - agent -c 'curl -fsSL https://astral.sh/uv/install.sh | sh'
su - agent -c 'export PATH="$HOME/.local/bin:$PATH"; uv python install 3.12'
su - agent -c 'command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
su - agent -c 'command -v opencode >/dev/null || curl -fsSL https://opencode.ai/install | bash'
grep -q '.opencode/bin' "$AGENT_HOME/.profile" 2>/dev/null || \
    echo 'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"' >>"$AGENT_HOME/.profile"

cat <<'MSG'

Listo. Falta, a mano y como `agent`:
  1. git clone del repo en tu Forgejo (token propio: solo ese repositorio; lectura/escritura de código y pull requests, sin administración).
  2. cd proxmox-gui/backend && uv venv --python 3.12 && uv pip install -r requirements.lock && uv pip install pytest pytest-asyncio respx ruff mypy pip-audit bandit
     cd ../frontend && pnpm install --frozen-lockfile
     (y poner backend/.venv/bin en PATH para el orquestador)
  3. ~/.pgui-lab.env (modo 0600, ver docs/hardening/BRIEF-OPENCODE.md) y /etc/pgui-orch/secrets.env (ver docs/hardening/ORCHESTRATOR.md).
  4. claude /login ; opencode auth login. El token de la forja va en /etc/pgui-orch/secrets.env como FORGEJO_TOKEN.
  5. python3 orchestrator/pgui_orch.py init && seal && dry-run
MSG
