#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/lxc/bootstrap.sh — runs INSIDE the freshly-created Debian 12 LXC as root,
# from a release tarball that install.sh already verified (signature + SHA-256).
#
# Required env (set by install.sh):
#   PGUI_RELEASE_TAG  release tag vX.Y.Z
#   PGUI_SRC_DIR      directory holding the extracted release (backend/ frontend/ deploy/
#                     requirements.lock) — there is NO git clone (F-04)
#
# Security model (F-02, F-06, F-09, F-10, F-12):
#   - /opt/proxmox-gui and everything in a release is root:root, never writable by the
#     service users; root only ever executes root-owned files.
#   - the backend is installed NON-editable from a hash-locked requirements.lock
#     (--require-hashes --only-binary=:all:); Node and Python come from pins.env
#     (URL + SHA-256), never `curl | sh`.
#   - proxmox-gui (API + worker) owns /var/lib/proxmox-gui only; proxmox-gui-web (Node)
#     has no access to /etc/proxmox-gui, Redis or /var/lib/proxmox-gui.
#   - secrets are root:proxmox-gui 0440 (the app reads them, cannot replace them).
#   - no sudoers; updates go through the root updater (deploy/host-lxc, P4).
#
# Idempotent: re-running with the same tag rebuilds that release directory; an
# existing install (marker present) refuses to run (use the updater).
#
# PGUI_ROOT (tests only) prefixes every filesystem path this script writes.

set -euo pipefail
trap 'echo "ERROR: bootstrap.sh failed at line $LINENO" >&2' ERR

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# ----------------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------------
RELEASE="${PGUI_RELEASE_TAG:-}"
APP_SRC="${PGUI_SRC_DIR:-}"
if [[ ! "$RELEASE" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ || "$RELEASE" == *..* ]]; then
    die "PGUI_RELEASE_TAG must be a release tag vX.Y.Z (got '${RELEASE}')."
fi
if [[ -z "$APP_SRC" || ! -d "$APP_SRC/backend" || ! -d "$APP_SRC/deploy" || ! -d "$APP_SRC/frontend" ]]; then
    die "PGUI_SRC_DIR must point at the extracted release (backend/ frontend/ deploy/)."
fi
ROOT="${PGUI_ROOT:-}"
if [[ -z "$ROOT" && "$(id -u)" -ne 0 ]]; then die "must run as root"; fi

# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------
APP_USER="proxmox-gui"
WEB_USER="proxmox-gui-web"
APP_HOME="${ROOT}/opt/proxmox-gui"
RELEASES_DIR="${APP_HOME}/releases"
CURRENT_LINK="${APP_HOME}/current"
REL_DIR="${RELEASES_DIR}/${RELEASE}"
ETC_DIR="${ROOT}/etc/proxmox-gui"
DATA_DIR="${ROOT}/var/lib/proxmox-gui"
LOG_DIR="${ROOT}/var/log/proxmox-gui"
SYSTEMD_DIR="${ROOT}/etc/systemd/system"
SBIN_DIR="${ROOT}/usr/local/sbin"
PY_DIR="${ROOT}/opt/python"
NODE_DIR="${ROOT}/opt/node"
INSTALLED_MARKER="${ETC_DIR}/.installed"

if [[ -f "$INSTALLED_MARKER" ]]; then
    die "$INSTALLED_MARKER exists: this LXC is already installed. Use the updater (install.sh --update on the host)."
fi

# ----------------------------------------------------------------------------
# Step 1: OS packages (no recommends; no compilers — wheels only)
# ----------------------------------------------------------------------------
info "apt: base packages..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    ca-certificates curl xz-utils tar sqlite3 openssl openssh-client \
    caddy redis-server iproute2

# ----------------------------------------------------------------------------
# Step 2: pinned toolchain (P3-05): download -> sha256sum -c -> extract
# ----------------------------------------------------------------------------
PINS_FILE="${APP_SRC}/deploy/pins.env"
[[ -f "$PINS_FILE" ]] || die "release lacks deploy/pins.env"
if grep -q 'TODO-PIN' "$PINS_FILE"; then die "deploy/pins.env still has TODO-PIN entries (run scripts/update-pins.sh)"; fi
# shellcheck disable=SC1090
source "$PINS_FILE"
for v in NODE_VERSION NODE_URL NODE_SHA256 PYTHON_VERSION PYTHON_URL PYTHON_SHA256; do
    [[ -n "${!v:-}" ]] || die "pins.env: $v is empty"
done
[[ "$NODE_SHA256" =~ ^[0-9a-f]{64}$ && "$PYTHON_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "pins.env: malformed SHA-256"
[[ "$NODE_URL" == https://* && "$PYTHON_URL" == https://* ]] || die "pins.env: URLs must be https://"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch_pinned() {  # fetch_pinned <url> <sha256> <dest-file>
    curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$3" "$1" || die "download failed: $1"
    printf '%s  %s\n' "$2" "$3" | sha256sum -c --status - || die "SHA-256 mismatch for $1 — refusing to install"
}

if [[ ! -x "${PY_DIR}/python/bin/python3.12" ]]; then
    info "Installing pinned Python ${PYTHON_VERSION}..."
    fetch_pinned "$PYTHON_URL" "$PYTHON_SHA256" "${WORK}/python.tar.gz"
    rm -rf "$PY_DIR"; mkdir -p "$PY_DIR"
    tar -xzf "${WORK}/python.tar.gz" -C "$PY_DIR" --no-same-owner
    chown -R root:root "$PY_DIR"; chmod -R u=rwX,go=rX "$PY_DIR"
fi
PYTHON_BIN="${PY_DIR}/python/bin/python3.12"

if [[ ! -x "${NODE_DIR}/bin/node" ]]; then
    info "Installing pinned Node ${NODE_VERSION}..."
    fetch_pinned "$NODE_URL" "$NODE_SHA256" "${WORK}/node.tar.xz"
    rm -rf "$NODE_DIR"; mkdir -p "$NODE_DIR"
    tar -xJf "${WORK}/node.tar.xz" -C "$NODE_DIR" --strip-components=1 --no-same-owner
    chown -R root:root "$NODE_DIR"; chmod -R u=rwX,go=rX "$NODE_DIR"
fi

# ----------------------------------------------------------------------------
# Step 3: service users (D6). No shell, no home; the web user shares nothing.
# ----------------------------------------------------------------------------
for u in "$APP_USER" "$WEB_USER"; do
    if ! id -u "$u" >/dev/null 2>&1; then
        info "Creating system user $u..."
        useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$u"
    fi
done
# Redis unix-socket access is granted through the `redis` group (D7).
usermod -aG redis "$APP_USER"

# ----------------------------------------------------------------------------
# Step 4: directories and modes
# ----------------------------------------------------------------------------
info "Creating directory layout..."
mkdir -p "$ETC_DIR" "$APP_HOME" "$RELEASES_DIR" "$DATA_DIR" "$DATA_DIR/update" "$DATA_DIR/backups" "$LOG_DIR" "$SBIN_DIR"
chown root:root "$APP_HOME" "$RELEASES_DIR"
chmod 0755 "$APP_HOME" "$RELEASES_DIR"
chown "root:${APP_USER}" "$ETC_DIR";  chmod 0750 "$ETC_DIR"
chown "${APP_USER}:${APP_USER}" "$DATA_DIR" "$DATA_DIR/update" "$DATA_DIR/backups" "$LOG_DIR"
chmod 0750 "$DATA_DIR" "$DATA_DIR/update" "$DATA_DIR/backups" "$LOG_DIR"

# ----------------------------------------------------------------------------
# Step 5: the release, immutable and root-owned (F-02)
# ----------------------------------------------------------------------------
info "Staging release ${RELEASE} into ${REL_DIR}..."
rm -rf "$REL_DIR"
mkdir -p "$REL_DIR"
for sub in backend frontend deploy; do
    cp -r "${APP_SRC}/${sub}" "${REL_DIR}/"
done
[[ -f "${APP_SRC}/backend/requirements.lock" ]] || die "release lacks backend/requirements.lock"
[[ -f "${REL_DIR}/frontend/build/index.js" ]] || die "release lacks frontend/build/index.js (built in CI, P6)"
chown -R root:root "$REL_DIR"
chmod -R u=rwX,go=rX "$REL_DIR"

# ----------------------------------------------------------------------------
# Step 6: Python venv, hash-locked, non-editable (F-02, F-10)
# ----------------------------------------------------------------------------
info "Creating venv and installing the hash-locked backend..."
"$PYTHON_BIN" -m venv "${REL_DIR}/.venv"
"${REL_DIR}/.venv/bin/pip" install --quiet --require-hashes --only-binary=:all: \
    -r "${REL_DIR}/backend/requirements.lock"
"${REL_DIR}/.venv/bin/pip" install --quiet --no-deps --no-build-isolation --no-index "${REL_DIR}/backend"
chown -R root:root "${REL_DIR}/.venv"
chmod -R u=rwX,go=rX "${REL_DIR}/.venv"

# ----------------------------------------------------------------------------
# Step 7: secrets (run from the root-owned release tree)
# ----------------------------------------------------------------------------
info "Generating master.key, jwt.secret, pat.pepper, setup-token (idempotent)..."
export PGUI_ETC_DIR="$ETC_DIR"
bash "${REL_DIR}/deploy/scripts/gen-master-key.sh"
bash "${REL_DIR}/deploy/scripts/gen-jwt-secret.sh"
bash "${REL_DIR}/deploy/scripts/gen-setup-token.sh"

# ----------------------------------------------------------------------------
# Step 8: migrations as the unprivileged service user
# ----------------------------------------------------------------------------
info "Running alembic upgrade head..."
cd "${REL_DIR}/backend"
runuser -u "$APP_USER" -- env \
    PROXMOX_GUI_DATABASE_URL="sqlite+aiosqlite:////var/lib/proxmox-gui/app.db" \
    "${REL_DIR}/.venv/bin/alembic" -c "${REL_DIR}/backend/alembic.ini" upgrade head
cd /

# ----------------------------------------------------------------------------
# Step 9: current symlink, units, Redis, Caddy
# ----------------------------------------------------------------------------
info "Pointing ${CURRENT_LINK} -> releases/${RELEASE}..."
ln -sfn "releases/${RELEASE}" "$CURRENT_LINK"

info "Installing systemd units (root-owned)..."
mkdir -p "$SYSTEMD_DIR" "${SYSTEMD_DIR}/redis-server.service.d" "${SYSTEMD_DIR}/caddy.service.d"
for unit in proxmox-gui-api.service proxmox-gui-worker.service proxmox-gui-frontend.service \
            proxmox-gui-caddy-render.service; do
    install -m 0644 "${REL_DIR}/deploy/systemd/${unit}" "${SYSTEMD_DIR}/${unit}"
done
install -m 0644 "${REL_DIR}/deploy/systemd/redis-server.service.d/proxmox-gui.conf" \
    "${SYSTEMD_DIR}/redis-server.service.d/proxmox-gui.conf"
install -m 0644 "${REL_DIR}/deploy/systemd/caddy.service.d/proxmox-gui.conf" \
    "${SYSTEMD_DIR}/caddy.service.d/proxmox-gui.conf"
# The updater units (path + service) are installed by the release itself (P4).
if [[ -d "${REL_DIR}/deploy/host-lxc" ]]; then
    install -m 0755 -o root -g root "${REL_DIR}/deploy/host-lxc/proxmox-gui-updater" "${SBIN_DIR}/proxmox-gui-updater"
    for unit in proxmox-gui-updater.path proxmox-gui-updater.service; do
        install -m 0644 "${REL_DIR}/deploy/systemd/${unit}" "${SYSTEMD_DIR}/${unit}"
    done
fi
install -m 0755 -o root -g root "${REL_DIR}/deploy/lxc/render-caddyfile.sh" "${SBIN_DIR}/proxmox-gui-caddy-render"

# Release pinning for the updater: fixed to THIS fork/tag source by the installer (D2/F-04).
if [[ -n "${PGUI_REPO_URL:-}" ]]; then
    [[ "$PGUI_REPO_URL" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid PGUI_REPO_URL"
    printf 'REPO_URL=%s\n' "$PGUI_REPO_URL" >"${ETC_DIR}/release.conf"
    chown "root:${APP_USER}" "${ETC_DIR}/release.conf"
    chmod 0644 "${ETC_DIR}/release.conf"
fi

info "Enabling services..."
systemctl daemon-reload
systemctl enable --now redis-server.service
systemctl restart redis-server.service
systemctl enable proxmox-gui-caddy-render.service caddy.service
systemctl restart caddy.service
systemctl enable --now proxmox-gui-api.service proxmox-gui-worker.service proxmox-gui-frontend.service
if [[ -d "${REL_DIR}/deploy/host-lxc" ]]; then
    systemctl enable --now proxmox-gui-updater.path
fi

# ----------------------------------------------------------------------------
# Step 10: marker (root-owned) + banner
# ----------------------------------------------------------------------------
: >"$INSTALLED_MARKER"
chown root:root "$INSTALLED_MARKER"
chmod 0644 "$INSTALLED_MARKER"

echo
echo "============================================================"
echo "  Bootstrap complete (release ${RELEASE})."
echo "  First-run wizard: https://<LXC-IP>/setup (needs the setup token:"
echo "  cat /etc/proxmox-gui/setup-token — as root inside this LXC)."
echo "  Logs: journalctl -u proxmox-gui-api -u proxmox-gui-frontend -u caddy -f"
echo "============================================================"
