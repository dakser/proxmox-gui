#!/usr/bin/env bash
# shellcheck shell=bash
# SC2016: hostile payloads stay literal on purpose.
# shellcheck disable=SC2016
# scripts/set-fork.sh (P6-04) and the "no upstream reference in executable code" acceptance.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env

copy_repo() {
    rm -rf "$T_TMP/r"; mkdir -p "$T_TMP/r/scripts" "$T_TMP/r/deploy"
    cp "$REPO_DIR/scripts/set-fork.sh" "$T_TMP/r/scripts/"
    cp "$REPO_DIR/deploy/install.sh" "$T_TMP/r/deploy/"
    cp "$REPO_DIR/README.md" "$T_TMP/r/"; cp "$REPO_DIR/deploy/README.md" "$T_TMP/r/deploy/"
    # simulate an un-forked checkout pointing at the upstream author
    sed -i 's#github.com/dakser/proxmox-gui#github.com/chloepriceless/proxmox-gui#g' "$T_TMP/r/deploy/install.sh" "$T_TMP/r/README.md" "$T_TMP/r/deploy/README.md"
}
setfork() { run_cmd bash -c "cd '$T_TMP/r' && scripts/set-fork.sh '$1'"; }

test_case "rewrites the installer default and the docs, idempotently"
copy_repo
setfork someone/my-pgui
assert_rc 0 "set-fork"
assert_contains "$(grep '^readonly DEFAULT_REPO_URL' "$T_TMP/r/deploy/install.sh")" 'DEFAULT_REPO_URL="https://github.com/someone/my-pgui"' "installer default"
assert_eq "0" "$(grep -c chloepriceless "$T_TMP/r/deploy/install.sh" "$T_TMP/r/README.md" "$T_TMP/r/deploy/README.md" | awk -F: '{s+=$2} END {print s}')" "no upstream reference left in installer/docs"
before="$(cat "$T_TMP/r/deploy/install.sh")"
setfork someone/my-pgui
assert_eq "$before" "$(cat "$T_TMP/r/deploy/install.sh")" "idempotent"
bash -n "$T_TMP/r/deploy/install.sh" && _ok=1 || _ok=0; assert_eq 1 "$_ok" "install.sh still parses"

test_case "hostile arguments"
for bad in "" "owner" "a/b/c" "a b/c" 'o/r;id' 'o/$(id)' "../x" "o/r.git" 'o/r"x' "o/"; do
    copy_repo; setfork "$bad"
    assert_rc_nonzero "slug '$bad'"
    assert_contains "$(grep '^readonly DEFAULT_REPO_URL' "$T_TMP/r/deploy/install.sh")" chloepriceless "installer untouched for '$bad'"
done

test_case "acceptance: no executable reference to the upstream author's repository in this repo"
cd "$REPO_DIR"
hits="$(grep -rIn "chloepriceless" --include='*.sh' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.conf' \
        --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=build --exclude-dir=.planning --exclude-dir=tests . || true)"
assert_eq "" "$hits" "grep -rn chloepriceless (executable file types)"
assert_eq "" "$(grep -rn "chloepriceless" deploy/install.sh deploy/lxc deploy/host-lxc deploy/systemd scripts || true)" "installer, bootstrap, updater, units"
assert_eq "" "$(grep -rn "chloepriceless" backend/app || true)" "backend code"

finish
