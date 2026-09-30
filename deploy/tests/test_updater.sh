#!/usr/bin/env bash
# shellcheck shell=bash
# Root updater inside the LXC (F-03/F-04/F-05): signature+hash, tarball validation, version
# policy, never touching the active release, backup, migration, health check and rollback.
# Real: ssh-keygen, tar, python3 (validator), sqlite3, sha256sum. Shims: curl, systemctl, runuser.
# SC2016: hostile payloads stay literal on purpose. SC2329: release_stage_hook is called indirectly by make_release.
# shellcheck disable=SC2016,SC2329
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env
UPD="$DEPLOY_DIR/host-lxc/proxmox-gui-updater"
R="$PGUI_ROOT"
export PGUI_PYTHON="$T_TMP/fakepython"
export PGUI_DOWNLOAD_BASE="https://example.test/rel"
export PGUI_HEALTH_ATTEMPTS=1 PGUI_WORKER_DELAY=0

# Fake interpreter: `-m venv DIR` creates DIR/bin/{pip,alembic}; validation runs the REAL python3.
cat >"$PGUI_PYTHON" <<'P'
#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    mkdir -p "$3/bin"
    printf '#!/usr/bin/env bash\necho "pip $*" >>"$SHIM_LOG"\n' >"$3/bin/pip"
    cat >"$3/bin/alembic" <<'A'
#!/usr/bin/env bash
echo "alembic $*" >>"$SHIM_LOG"
sqlite3 "$PGUI_ROOT/var/lib/proxmox-gui/app.db" "INSERT INTO marks VALUES ('migrated');"
[[ -e "$T_TMP/alembic_fail" ]] && exit 1
exit 0
A
    chmod +x "$3/bin/pip" "$3/bin/alembic"
    exit 0
fi
exec python3 "$@"
P
chmod +x "$PGUI_PYTHON"

fresh() {  # fresh [current-tag]: installed LXC state at <current-tag> (default v0.1.0)
    local cur="${1:-v0.1.0}"
    reset_env
    export SHIM_CFG SHIM_LOG PGUI_ROOT
    unset -f release_stage_hook 2>/dev/null || true
    rm -f "$T_TMP/alembic_fail" "$T_TMP/health_fail"
    mkdir -p "$R/opt/proxmox-gui/releases/$cur" "$R/etc/proxmox-gui" "$R/var/lib/proxmox-gui/update" \
        "$R/var/lib/proxmox-gui/backups" "$R/etc/systemd/system" "$R/usr/local/sbin"
    echo "old" >"$R/opt/proxmox-gui/releases/$cur/marker"
    ln -sfn "releases/$cur" "$R/opt/proxmox-gui/current"
    echo "REPO_URL=https://github.com/o/r" >"$R/etc/proxmox-gui/release.conf"
    printf 'NODE_SHA256=aa\nPYTHON_SHA256=bb\n' >"$R/etc/proxmox-gui/pins.env"
    rm -f "$T_TMP/app.db"
    sqlite3 "$R/var/lib/proxmox-gui/app.db" "CREATE TABLE marks(v TEXT); INSERT INTO marks VALUES ('before');"
    make_release v0.2.0
    cp "$SIGNERS" "$R/etc/proxmox-gui/release-signers"
    shim_handler runuser <<'H'
        shift 3   # -u <user> --
        "$@"
H
    curl_handler_keep
}
# make_release installs the curl shim; keep a reference so tests can replace it and restore it.
curl_handler_keep() { :; }

request() { printf '%s' "$1" >"$R/var/lib/proxmox-gui/update/request"; }
apply() { run_cmd bash "$UPD" apply "$@"; }
status_json() { cat "$R/run/proxmox-gui-updater/status.json"; }
jget() { status_json | python3 -c "import json,sys; v=json.load(sys.stdin)['$1']; print(str(v).lower() if isinstance(v, bool) else v)"; }
cur() { basename "$(readlink "$R/opt/proxmox-gui/current")"; }
db_marks() { sqlite3 "$R/var/lib/proxmox-gui/app.db" "SELECT group_concat(v) FROM marks;"; }
assert_unchanged() {  # assert_unchanged <tag> <why>
    assert_eq "$1" "$(cur)" "$2: active release unchanged"
    assert_eq "old" "$(cat "$R/opt/proxmox-gui/releases/$1/marker")" "$2: active release content untouched"
}

test_case "successful update v0.1.0 -> v0.2.0"
fresh; request v0.2.0
apply
assert_rc 0 "apply"
assert_eq "v0.2.0" "$(cur)" "current switched"
assert_eq "v0.1.0" "$(basename "$(readlink "$R/opt/proxmox-gui/previous")")" "previous points at the old release"
assert_eq "succeeded" "$(jget state)" "status state"
assert_eq "v0.2.0" "$(jget target)" "status target"; assert_eq "v0.1.0" "$(jget from)" "status from"
assert_no_file "$R/var/lib/proxmox-gui/update/request" "request consumed"
assert_eq "old" "$(cat "$R/opt/proxmox-gui/releases/v0.1.0/marker")" "old release untouched"
NEWR="$R/opt/proxmox-gui/releases/v0.2.0"
assert_file "$NEWR/frontend/build/index.js" "new release extracted"
assert_eq "" "$(find "$NEWR" -perm /022 -not -type l | head -3)" "new release not group/other-writable"
assert_logged "pip install --quiet --require-hashes --only-binary=:all: -r $NEWR/backend/requirements.lock" "hash-locked deps"
assert_logged "--no-deps --no-build-isolation --no-index $NEWR/backend" "non-editable install"
assert_not_contains "$(grep '^pip' "$SHIM_LOG")" "--upgrade" "no pip upgrade"
assert_logged "alembic -c $NEWR/backend/alembic.ini upgrade head" "migration with the NEW venv"
assert_logged "runuser -u proxmox-gui -- env PROXMOX_GUI_DATABASE_URL=" "migration as the unprivileged user"
assert_eq "before,migrated" "$(db_marks)" "database migrated"
bk="$(ls "$R"/var/lib/proxmox-gui/backups/app-*-pre-v0.2.0.db)"
assert_eq "before" "$(sqlite3 "$bk" "SELECT group_concat(v) FROM marks;")" "consistent pre-update backup"
order="$(grep -n '^systemctl restart' "$SHIM_LOG" | sed 's/^[0-9]*://' | tr '\n' '|')"
assert_eq "systemctl restart proxmox-gui-api.service|systemctl restart proxmox-gui-frontend.service|systemctl restart proxmox-gui-worker.service|" "$order" "restart order: api, frontend, worker LAST"
assert_contains "$(cat "$R/etc/proxmox-gui/pins.env")" "NODE_SHA256=aa" "toolchain record kept"
assert_not_logged "sudo" "no sudo"

test_case "invalid signature aborts before extracting anything"
fresh; request v0.2.0; printf 'garbage' >"$REL_DIR/SHA256SUMS.sig"
apply
assert_rc_nonzero "bad signature"; assert_eq "failed" "$(jget state)" "status failed"
assert_unchanged v0.1.0 "bad signature"; assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0" "nothing extracted"
assert_not_logged "alembic" "no migration"; assert_eq "before" "$(db_marks)" "database untouched"

test_case "signature by a different key aborts"
fresh; request v0.2.0
ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/other" -C other
(cd "$REL_DIR" && rm SHA256SUMS.sig && ssh-keygen -Y sign -q -f "$T_TMP/other" -n proxmox-gui-release SHA256SUMS)
apply
assert_rc_nonzero "wrong signer"; assert_unchanged v0.1.0 "wrong signer"; assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0"

test_case "tampered tarball (hash mismatch) aborts"
fresh; request v0.2.0; printf 'evil' >>"$REL_DIR/proxmox-gui-v0.2.0.tar.gz"
apply
assert_rc_nonzero "hash mismatch"; assert_contains "$(jget message)" "SHA-256 mismatch" "message"
assert_unchanged v0.1.0 "hash mismatch"; assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0"

test_case "hostile / malformed requests are rejected without any download"
i=0
for bad in "." ".." "a b" '$(id)' 'v1.0.0;id' $'v1.0.0\nv9.9.9' "v1.0" "1.2.3" "master" "latest" "" "v1..0.0" "../v1.0.0" "v1.0.0/../x" \
           "v1.0.0-$(printf 'a%.0s' {1..70})" "v1.0.0 " " v1.0.0"; do
    i=$((i + 1)); fresh; request "$bad"
    apply
    assert_rc_nonzero "request #$i"
    assert_eq "failed" "$(jget state)" "request #$i state"
    assert_no_file "$R/var/lib/proxmox-gui/update/request" "request #$i consumed even when invalid"
    assert_not_logged "curl" "request #$i: no download"; assert_unchanged v0.1.0 "request #$i"
done

test_case "no request and no --tag: nothing happens"
fresh
apply
assert_rc 0 "idle"; assert_not_logged "curl" "no download"; assert_unchanged v0.1.0 "idle"

test_case "oversized request file (> 64 bytes) is rejected"
fresh; request "v0.2.0$(printf ' %.0s' {1..80})"
apply
assert_rc_nonzero "oversized request"; assert_unchanged v0.1.0 "oversized"

test_case "unsafe tarball members are refused before extraction"
declare -A EVIL=(
    [traversal]='add("../evil","file")'
    [absolute]='add("/tmp/evil","file")'
    [dotdot-inner]='add("backend/../../evil","file")'
    [symlink-abs]='add("backend/link","sym","/etc")'
    [symlink-escape]='add("backend/link","sym","../../../etc")'
    [hardlink]='add("backend/hl","hard","backend/pyproject.toml")'
    [chardev]='add("backend/dev","chr")'
    [fifo]='add("backend/pipe","fifo")'
    [setuid]='add("backend/su","suid")'
)
for name in "${!EVIL[@]}"; do
    fresh; request v0.2.0
    python3 - "$T_TMP/stage-evil.tar.gz" "${EVIL[$name]}" <<'PY'
import io, sys, tarfile
out, action = sys.argv[1], sys.argv[2]
tf = tarfile.open(out, "w:gz")
def add(name, kind, target=""):
    ti = tarfile.TarInfo(name)
    if kind == "file":
        ti.size = 1; tf.addfile(ti, io.BytesIO(b"x"))
    elif kind == "suid":
        ti.size = 1; ti.mode = 0o4755; tf.addfile(ti, io.BytesIO(b"x"))
    elif kind == "sym":
        ti.type = tarfile.SYMTYPE; ti.linkname = target; tf.addfile(ti)
    elif kind == "hard":
        ti.type = tarfile.LNKTYPE; ti.linkname = target; tf.addfile(ti)
    elif kind == "chr":
        ti.type = tarfile.CHRTYPE; ti.devmajor = 1; ti.devminor = 5; tf.addfile(ti)
    elif kind == "fifo":
        ti.type = tarfile.FIFOTYPE; tf.addfile(ti)
for d in ("backend", "frontend", "frontend/build", "deploy"):
    ti = tarfile.TarInfo(d); ti.type = tarfile.DIRTYPE; ti.mode = 0o755; tf.addfile(ti)
for n in ("backend/requirements.lock", "frontend/build/index.js", "deploy/pins.env"):
    ti = tarfile.TarInfo(n); ti.size = 1; tf.addfile(ti, io.BytesIO(b"x"))
eval(action)
tf.close()
PY
    cp "$T_TMP/stage-evil.tar.gz" "$REL_DIR/proxmox-gui-v0.2.0.tar.gz"; sign_release v0.2.0
    apply
    assert_rc_nonzero "evil tarball: $name"
    assert_contains "$(jget message)" "unsafe tarball" "evil tarball $name: message"
    assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0" "evil tarball $name: nothing extracted"
    assert_no_file "$T_TMP/evil" "evil tarball $name: no file escaped"; assert_no_file "/tmp/evil" "evil tarball $name: no file escaped to /tmp"
    assert_unchanged v0.1.0 "evil tarball $name"; assert_not_logged "alembic" "evil tarball $name: no migration"
done

test_case "same version as the active release is a no-op"
fresh; request v0.1.0
apply
assert_rc 0 "noop"; assert_eq "noop" "$(jget state)" "state noop"; assert_not_logged "curl" "no download"; assert_unchanged v0.1.0 "noop"

test_case "downgrade is refused from a request and allowed only via --allow-downgrade"
fresh v0.3.0; request v0.2.0
apply
assert_rc_nonzero "downgrade via request"; assert_contains "$(jget message)" "downgrade" "message"
assert_unchanged v0.3.0 "downgrade refused"; assert_not_logged "curl" "no download"
fresh v0.3.0; request v0.2.0
apply --allow-downgrade
assert_rc_nonzero "a request-file update can never be a downgrade, even if the flag is passed"
fresh v0.3.0
apply --tag v0.2.0
assert_rc_nonzero "downgrade via CLI without the flag"
fresh v0.3.0
apply --tag v0.2.0 --allow-downgrade
assert_rc 0 "downgrade via CLI with the flag"; assert_eq "v0.2.0" "$(cur)" "downgraded from the root console"

test_case "a prerelease is older than its release (semver)"
fresh v1.0.0; make_release v1.0.0-rc1; cp "$SIGNERS" "$R/etc/proxmox-gui/release-signers"
apply --tag v1.0.0-rc1
assert_rc_nonzero "release -> prerelease is a downgrade"

test_case "migration failure: database restored, new release removed, active release untouched"
fresh; request v0.2.0; touch "$T_TMP/alembic_fail"
apply
assert_rc_nonzero "migration failure"; assert_eq "failed" "$(jget state)" "state"
assert_eq "true" "$(jget rolled_back)" "rolled_back flag"
assert_eq "before" "$(db_marks)" "database restored from the pre-update backup (the migration's write is gone)"
assert_unchanged v0.1.0 "migration failure"; assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0" "failed release removed"
assert_logged "systemctl stop proxmox-gui-api.service proxmox-gui-worker.service" "services stopped before the restore"

test_case "no migration started => no database restore (a download failure must not touch the DB)"
fresh; request v0.2.0; rm "$REL_DIR/proxmox-gui-v0.2.0.tar.gz"
sqlite3 "$R/var/lib/proxmox-gui/app.db" "INSERT INTO marks VALUES ('live-write');"
apply
assert_rc_nonzero "download failure"; assert_eq "before,live-write" "$(db_marks)" "database untouched"
assert_not_logged "systemctl stop" "services untouched"

test_case "health-check failure: rolled back to the previous release with the database restored"
fresh; request v0.2.0; touch "$T_TMP/health_fail"
apply
assert_rc_nonzero "health failure"; assert_eq "failed" "$(jget state)" "state"
assert_unchanged v0.1.0 "health failure"; assert_eq "before" "$(db_marks)" "database restored"
assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0" "failed release removed"
assert_eq "3" "$(grep -c '^systemctl restart' "$SHIM_LOG" | awk '{print ($1>=3)?3:$1}')" "services restarted on the old release"

test_case "release that changes the pinned toolchain stops the update"
fresh; request v0.2.0; printf 'NODE_SHA256=cc\nPYTHON_SHA256=bb\n' >"$R/etc/proxmox-gui/pins.env"
apply
assert_rc_nonzero "toolchain change"; assert_contains "$(jget message)" "toolchain" "message"
assert_not_logged "alembic" "no migration"; assert_unchanged v0.1.0 "toolchain change"

test_case "structurally invalid releases are refused"
fresh; request v0.2.0
release_stage_hook() { rm -f "$1/frontend/build/index.js"; }
make_release v0.2.0; cp "$SIGNERS" "$R/etc/proxmox-gui/release-signers"
apply
assert_rc_nonzero "no frontend build"; assert_unchanged v0.1.0 "no frontend build"; assert_not_logged "alembic" "no migration"
fresh; request v0.2.0
release_stage_hook() { mkdir -p "$1/.venv/bin"; echo x >"$1/.venv/bin/python"; }
make_release v0.2.0; cp "$SIGNERS" "$R/etc/proxmox-gui/release-signers"
apply
assert_rc_nonzero "shipped .venv"; assert_unchanged v0.1.0 "shipped venv"
fresh; request v0.2.0
release_stage_hook() { rm -f "$1/backend/requirements.lock"; }
make_release v0.2.0; cp "$SIGNERS" "$R/etc/proxmox-gui/release-signers"
apply
assert_rc_nonzero "no lockfile"; assert_unchanged v0.1.0 "no lockfile"
unset -f release_stage_hook

test_case "the active or previous release directory is never overwritten"
fresh v0.1.0; mkdir -p "$R/opt/proxmox-gui/releases/v0.2.0"; echo "prev" >"$R/opt/proxmox-gui/releases/v0.2.0/marker"
ln -sfn releases/v0.2.0 "$R/opt/proxmox-gui/previous"; request v0.2.0
apply
assert_rc_nonzero "target is the previous release"; assert_eq "prev" "$(cat "$R/opt/proxmox-gui/releases/v0.2.0/marker")" "previous release content untouched"
assert_unchanged v0.1.0 "previous overwrite refused"

test_case "a leftover directory from an earlier failed attempt is replaced"
fresh; mkdir -p "$R/opt/proxmox-gui/releases/v0.2.0"; echo leftover >"$R/opt/proxmox-gui/releases/v0.2.0/junk"; request v0.2.0
apply
assert_rc 0 "apply over leftover"; assert_no_file "$R/opt/proxmox-gui/releases/v0.2.0/junk" "leftover removed"

test_case "retention: current + previous + one more; never the active/previous"
fresh v0.1.0
for t in v0.0.1 v0.0.2 v0.0.3; do mkdir -p "$R/opt/proxmox-gui/releases/$t"; done
request v0.2.0
apply
assert_rc 0 "apply"
left="$(find "$R/opt/proxmox-gui/releases" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort | tr '\n' ' ')"
assert_eq "v0.0.3 v0.1.0 v0.2.0 " "$left" "kept the 3 most recent (incl. active + previous)"

test_case "manual rollback command"
fresh; request v0.2.0; apply
assert_eq "v0.2.0" "$(cur)" "updated first"
run_cmd bash "$UPD" rollback
assert_rc 0 "rollback"; assert_eq "v0.1.0" "$(cur)" "rolled back to the previous release"
assert_eq "before,migrated" "$(db_marks)" "database untouched without --restore-db"
run_cmd bash "$UPD" rollback --restore-db
assert_rc 0 "rollback again (forward) with restore"; assert_eq "before" "$(db_marks)" "database restored from the last pre-update backup"

test_case "status subcommand"
run_cmd bash "$UPD" status
assert_rc 0 "status"; assert_contains "$OUT" "current:" "prints current"

finish
