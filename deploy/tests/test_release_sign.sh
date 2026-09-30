#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/release-sign.sh: signs only what is really in the draft, verifies with the embedded signer key,
# refuses the placeholder and a wrong key, never uploads on failure. Real ssh-keygen; `gh` is a shim.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
setup_env

# Work in a throw-away copy of the repo pieces the script reads (it cd's to the repo root).
prepare() {  # prepare [placeholder|real]
    reset_env
    rm -rf "$T_TMP/repo"; mkdir -p "$T_TMP/repo/scripts" "$T_TMP/repo/deploy" "$T_TMP/draft"
    cp "$REPO_DIR/scripts/release-sign.sh" "$REPO_DIR/scripts/sync-signers.sh" "$T_TMP/repo/scripts/"
    cp "$REPO_DIR/deploy/install.sh" "$T_TMP/repo/deploy/"
    (cd "$T_TMP/repo" && git init -q && git remote add origin https://github.com/dakser/proxmox-gui)
    [[ -f "$T_TMP/owner_key" ]] || ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/owner_key" -C owner
    [[ -f "$T_TMP/other_key" ]] || ssh-keygen -t ed25519 -N "" -q -f "$T_TMP/other_key" -C other
    if [[ "${1:-real}" == placeholder ]]; then
        cp "$REPO_DIR/deploy/release-signers" "$T_TMP/repo/deploy/release-signers"
    else
        printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "$(cut -d' ' -f1,2 "$T_TMP/owner_key.pub")" >"$T_TMP/repo/deploy/release-signers"
        (cd "$T_TMP/repo" && scripts/sync-signers.sh >/dev/null)
    fi
    echo "tarball-bytes" >"$T_TMP/draft/proxmox-gui-v0.7.0.tar.gz"
    echo "installer" >"$T_TMP/draft/install.sh"
    (cd "$T_TMP/draft" && sha256sum proxmox-gui-v0.7.0.tar.gz install.sh >SHA256SUMS)
    rm -rf "$T_TMP/uploads"; mkdir -p "$T_TMP/uploads"
    shim_handler gh <<'H'
        case "$1 $2" in
            "release download")
                local dir="" ; local prev=""
                for a in "$@"; do [[ "$prev" == "--dir" ]] && dir="$a"; prev="$a"; done
                cp "$T_TMP"/draft/* "$dir/" ;;
            "release upload") cp "$4" "$T_TMP/uploads/" ;;
            "release edit") : ;;
        esac
        return 0
H
}
sign() { run_cmd bash -c "cd '$T_TMP/repo' && scripts/release-sign.sh v0.7.0 $*"; }

test_case "signs the draft, verifies with the embedded key, uploads SHA256SUMS.sig"
prepare
sign --key "$T_TMP/owner_key"
assert_rc 0 "sign"
assert_file "$T_TMP/uploads/SHA256SUMS.sig" "signature uploaded"
assert_logged "gh release upload v0.7.0" "uploaded to the tag"
assert_logged "--repo dakser/proxmox-gui" "repository taken from origin"
assert_not_logged "release edit" "not published without --publish"
ssh-keygen -Y verify -f <(printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "$(cut -d' ' -f1,2 "$T_TMP/owner_key.pub")") \
    -I proxmox-gui-release -n proxmox-gui-release -s "$T_TMP/uploads/SHA256SUMS.sig" <"$T_TMP/draft/SHA256SUMS" >/dev/null && _ok=1 || _ok=0
assert_eq 1 "$_ok" "the uploaded signature verifies over the draft's SHA256SUMS in the right namespace"

test_case "--publish publishes only after a successful upload"
prepare
sign --key "$T_TMP/owner_key" --publish
assert_rc 0 "sign+publish"; assert_logged "gh release edit v0.7.0" "published"; assert_logged "--draft=false" "draft flag cleared"

test_case "a wrong key is refused and nothing is uploaded"
prepare
sign --key "$T_TMP/other_key"
assert_rc_nonzero "wrong key"; assert_no_file "$T_TMP/uploads/SHA256SUMS.sig" "nothing uploaded"; assert_not_logged "release upload" "no upload"

test_case "a tampered draft (hash mismatch) is never signed"
prepare
echo "evil" >"$T_TMP/draft/proxmox-gui-v0.7.0.tar.gz"
sign --key "$T_TMP/owner_key"
assert_rc_nonzero "tampered draft"; assert_contains "$ERR" "hash mismatch" "message"; assert_no_file "$T_TMP/uploads/SHA256SUMS.sig"

test_case "the placeholder signer key blocks signing"
prepare placeholder
sign --key "$T_TMP/owner_key"
assert_rc_nonzero "placeholder"; assert_contains "$ERR" "placeholder" "message"; assert_not_logged "gh" "gh never called"

test_case "install.sh out of sync with release-signers blocks signing"
prepare
sed -i 's/^proxmox-gui-release namespaces.*$/proxmox-gui-release namespaces="proxmox-gui-release" ssh-ed25519 AAAAdifferent/' "$T_TMP/repo/deploy/install.sh"
sign --key "$T_TMP/owner_key"
assert_rc_nonzero "out of sync"; assert_no_file "$T_TMP/uploads/SHA256SUMS.sig"

test_case "hostile arguments"
prepare
for t in "v1" "master" 'v1.0.0;id' "../v1.0.0" ""; do
    run_cmd bash -c "cd '$T_TMP/repo' && scripts/release-sign.sh '$t' --key '$T_TMP/owner_key'"
    assert_rc_nonzero "tag '$t'"
done
sign --key
assert_rc_nonzero "--key without a value"
sign --key "$T_TMP/owner_key" --repo 'evil;x'
assert_rc_nonzero "hostile --repo"; assert_not_logged "gh" "gh not called for a hostile repo"

finish
