#!/usr/bin/env bash
# shellcheck shell=bash
# bootstrap.sh file model and privileges (F-02, F-06, F-09, F-10, F-12).
# SC2016 is disabled on purpose: hostile payloads must stay literal (unexpanded).
# shellcheck disable=SC2016
# Runs the real script under PGUI_ROOT with shims for apt/systemctl/useradd/chown/runuser/curl
# and a fake python/pip, so it checks WHAT is done (modes, ownership calls, pip flags, units),
# not that real services start (that is the systemd smoke test, P7-02).
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
BOOT="$DEPLOY_DIR/lxc/bootstrap.sh"
R="$PGUI_ROOT"

build_source() {  # build_source: fake release tree + fake pinned toolchain archives
    rm -rf "$T_TMP/src" "$T_TMP/dl"; mkdir -p "$T_TMP/src/backend" "$T_TMP/src/frontend/build" "$T_TMP/dl" "$T_TMP/py/python/bin" "$T_TMP/nd/node-x/bin"
    cp -r "$DEPLOY_DIR" "$T_TMP/src/deploy"; rm -rf "$T_TMP/src/deploy/tests"
    echo "pkg==1.0 --hash=sha256:00" >"$T_TMP/src/backend/requirements.lock"
    echo "[alembic]" >"$T_TMP/src/backend/alembic.ini"; echo x >"$T_TMP/src/backend/pyproject.toml"
    echo "console.log(1)" >"$T_TMP/src/frontend/build/index.js"
    cat >"$T_TMP/py/python/bin/python3.12" <<'P'
#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    mkdir -p "$3/bin"
    printf '#!/usr/bin/env bash\necho "pip $*" >>"$SHIM_LOG"\n' >"$3/bin/pip"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$3/bin/alembic"
    chmod +x "$3/bin/pip" "$3/bin/alembic"
fi
P
    chmod +x "$T_TMP/py/python/bin/python3.12"; printf '#!/bin/sh\n' >"$T_TMP/nd/node-x/bin/node"; chmod +x "$T_TMP/nd/node-x/bin/node"
    tar -czf "$T_TMP/dl/python.tar.gz" -C "$T_TMP/py" python
    tar -cJf "$T_TMP/dl/node.tar.xz" -C "$T_TMP/nd" node-x
    cat >"$T_TMP/src/deploy/pins.env" <<EOF
NODE_VERSION=22.0.0
NODE_URL=https://example.test/node.tar.xz
NODE_SHA256=$(sha256sum "$T_TMP/dl/node.tar.xz" | cut -d' ' -f1)
PYTHON_VERSION=3.12.0
PYTHON_URL=https://example.test/python.tar.gz
PYTHON_SHA256=$(sha256sum "$T_TMP/dl/python.tar.gz" | cut -d' ' -f1)
EOF
}

fresh() {
    reset_env; build_source
    export FAKE_DL="$T_TMP/dl"
    shim_handler curl <<'H'
        local out="" url="" prev=""
        for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; [[ "$a" == http* ]] && url="$a"; prev="$a"; done
        local src="$FAKE_DL/$(basename "$url")"
        [[ -f "$src" ]] || return 22
        cp "$src" "$out"
H
}
echo "proxmox-gui-release namespaces=\"proxmox-gui-release\" ssh-ed25519 AAAAtest" >"$T_TMP/signers"
boot() { run_cmd env PGUI_RELEASE_TAG=v0.0.1 PGUI_SRC_DIR="$T_TMP/src" PGUI_REPO_URL=https://github.com/o/r PGUI_SIGNERS_FILE="$T_TMP/signers" bash "$BOOT"; }

test_case "successful bootstrap: layout, modes and privileges"
fresh; boot
assert_rc 0 "bootstrap"
REL="$R/opt/proxmox-gui/releases/v0.0.1"
assert_file "$REL/frontend/build/index.js" "release staged"
assert_eq "" "$(find "$R/opt/proxmox-gui" -perm /022 -not -type l | head -3)" "nothing group/other-writable under /opt/proxmox-gui"
assert_logged "chown -R root:root $REL" "release tree handed to root"
assert_not_logged "chown -R proxmox-gui" "no recursive chown to the service user"
assert_not_logged "chown proxmox-gui:proxmox-gui $R/opt" "service user owns nothing under /opt"
assert_eq "" "$(grep '^chown' "$SHIM_LOG" | grep "/opt/proxmox-gui" | grep -v 'root:root' || true)" "every chown under /opt/proxmox-gui is to root:root"
for f in master.key jwt.secret pat.pepper setup-token; do assert_mode "$R/etc/proxmox-gui/$f" 440; done
assert_eq "32" "$(stat -c %s "$R/etc/proxmox-gui/master.key")" "master.key is 32 bytes"
assert_logged "chown root:proxmox-gui" "secrets are root:proxmox-gui"
assert_mode "$R/etc/proxmox-gui" 750
assert_mode "$R/var/lib/proxmox-gui" 750
assert_mode "$R/var/lib/proxmox-gui-updater" 700
assert_no_file "$R/var/lib/proxmox-gui/backups" "backups are not kept in the app-owned directory"
assert_logged "chown proxmox-gui:proxmox-gui $R/var/lib/proxmox-gui" "data dir owned by the service user"
assert_no_file "$R/etc/proxmox-gui/gui_ed25519" "no SSH key unless community-scripts is enabled"
assert_file "$R/etc/proxmox-gui/.installed" "marker"
assert_eq "v0.0.1" "$(basename "$(readlink "$R/opt/proxmox-gui/current")")" "current -> release"
assert_contains "$(cat "$R/etc/proxmox-gui/release.conf")" "REPO_URL=https://github.com/o/r" "release.conf pinned to the installing repo"
assert_contains "$(cat "$R/etc/proxmox-gui/release-signers")" "proxmox-gui-release" "signer trust anchor delivered for the updater"
assert_file "$R/etc/proxmox-gui/pins.env" "installed toolchain recorded for the updater"
assert_mode "$R/usr/local/sbin/proxmox-gui-updater" 755
assert_file "$R/etc/systemd/system/proxmox-gui-updater.path" "updater path unit"
assert_logged "systemctl enable --now proxmox-gui-updater.path" "updater path unit enabled"

test_case "no editable install, hashes required, no pip upgrade, no unpinned downloads"
assert_logged "pip install --quiet --require-hashes --only-binary=:all: -r $REL/backend/requirements.lock" "hash-locked install"
assert_logged "--no-deps --no-build-isolation --no-index $REL/backend" "project installed non-editable without deps"
assert_not_contains "$(grep '^pip' "$SHIM_LOG")" " -e " "no editable install"
assert_not_contains "$(grep '^pip' "$SHIM_LOG")" "--upgrade" "no pip/setuptools/wheel upgrade"
assert_eq "2" "$(grep -c '^curl' "$SHIM_LOG")" "only the two pinned downloads"
assert_not_contains "$(cat "$SHIM_LOG")" "astral.sh" "no uv installer"
assert_not_contains "$(cat "$SHIM_LOG")" "| sh" "no curl|sh"
assert_not_logged "apt-get install -y -qq --no-install-recommends ca-certificates curl xz-utils tar sqlite3 openssl openssh-client caddy redis-server iproute2 nodejs" "no distro nodejs"
assert_not_contains "$(grep '^apt-get' "$SHIM_LOG")" "nodejs" "Node comes from pins.env"
assert_not_contains "$(grep '^apt-get' "$SHIM_LOG")" "build-essential" "no compilers"

test_case "no sudo anywhere"
assert_no_file "$R/etc/sudoers.d" "no sudoers.d"
assert_not_logged "visudo" "visudo not used"
assert_not_contains "$(grep -rn 'sudo' "$R/etc/systemd/system" || true)" "sudo" "no sudo in units"

test_case "users and migrations"
assert_logged "useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin proxmox-gui" "service user"
assert_logged "useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin proxmox-gui-web" "separate web user"
assert_logged "usermod -aG redis proxmox-gui" "redis socket group"
assert_not_logged "usermod -aG redis proxmox-gui-web" "web user has no Redis access"
assert_logged "runuser -u proxmox-gui -- env PROXMOX_GUI_DATABASE_URL=sqlite+aiosqlite:////var/lib/proxmox-gui/app.db $REL/.venv/bin/alembic -c $REL/backend/alembic.ini upgrade head" "migrations run unprivileged, no bash -c"
assert_not_contains "$(grep '^runuser' "$SHIM_LOG")" "bash" "no shell interpolation for runuser"

test_case "units installed root-owned and Redis/Caddy drop-ins present"
for u in proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend proxmox-gui-caddy-render; do
    assert_mode "$R/etc/systemd/system/$u.service" 644
done
assert_file "$R/etc/systemd/system/redis-server.service.d/proxmox-gui.conf" "redis drop-in"
assert_file "$R/etc/systemd/system/caddy.service.d/proxmox-gui.conf" "caddy drop-in"
assert_contains "$(cat "$R/etc/systemd/system/redis-server.service.d/proxmox-gui.conf")" "--port 0" "redis has no TCP port"
assert_mode "$R/usr/local/sbin/proxmox-gui-caddy-render" 755
assert_logged "systemctl enable --now redis-server.service" "redis enabled"

test_case "re-running on an installed LXC is refused"
boot
assert_rc_nonzero "second bootstrap"; assert_contains "$ERR" "already installed" "message"

test_case "tampered pinned download aborts before anything is extracted or installed"
fresh; printf 'evil' >>"$T_TMP/dl/node.tar.xz"
boot
assert_rc_nonzero "hash mismatch"; assert_contains "$ERR" "SHA-256 mismatch" "message"
assert_no_file "$R/opt/node" "nothing extracted"
assert_no_file "$R/etc/proxmox-gui/.installed" "no marker"

test_case "TODO-PIN in pins.env aborts"
fresh; sed -i 's/^NODE_SHA256=.*/NODE_SHA256=TODO-PIN/' "$T_TMP/src/deploy/pins.env"
boot
assert_rc_nonzero "TODO-PIN"; assert_no_file "$R/etc/proxmox-gui/.installed"

test_case "hostile tag / source dir rejected"
for tag in 'v1.0.0;id' 'v1.0.0$(id)' 'master' '' 'v1..0.0' '../v1.0.0'; do
    fresh
    set +e; PGUI_RELEASE_TAG="$tag" PGUI_SRC_DIR="$T_TMP/src" bash "$BOOT" >/dev/null 2>&1; RC=$?; set -e
    assert_rc_nonzero "tag '$tag'"
    assert_no_file "$R/etc/proxmox-gui" "nothing created for tag '$tag'"
done
fresh
set +e; PGUI_RELEASE_TAG=v1.0.0 PGUI_SRC_DIR="/nonexistent" bash "$BOOT" >/dev/null 2>&1; RC=$?; set -e
assert_rc_nonzero "missing source dir"

test_case "a release without a frontend build or lockfile is refused"
fresh; rm "$T_TMP/src/frontend/build/index.js"; boot
assert_rc_nonzero "no frontend build"
fresh; rm "$T_TMP/src/backend/requirements.lock"; boot
assert_rc_nonzero "no lockfile"

finish
