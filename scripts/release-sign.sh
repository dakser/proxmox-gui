#!/usr/bin/env bash
# shellcheck shell=bash
# Sign a release draft LOCALLY (D2/D3). The signing key never leaves your machine and CI never sees it.
#
# Usage: scripts/release-sign.sh vX.Y.Z [--key PATH] [--repo owner/repo] [--publish]
#   --key PATH     private key for ssh-keygen -Y sign (default: ~/.ssh/proxmox-gui-release)
#   --repo O/R     GitHub repository (default: derived from `git remote get-url origin`)
#   --publish      after uploading SHA256SUMS.sig, turn the draft into a published release
#
# Steps: download the draft's SHA256SUMS + tarball + install.sh with `gh`; re-check every hash listed
# (you only ever sign what is really in the draft); sign SHA256SUMS in the namespace install.sh and
# the in-LXC updater verify (proxmox-gui-release); verify the signature against deploy/release-signers
# (the public key embedded in install.sh); upload SHA256SUMS.sig.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TAG="${1:-}"; [[ $# -gt 0 ]] && shift
KEY="${HOME}/.ssh/proxmox-gui-release"
REPO=""
PUBLISH=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key)  [[ $# -ge 2 ]] || { echo "ERROR: --key needs a value" >&2; exit 2; }; KEY="$2"; shift 2 ;;
        --repo) [[ $# -ge 2 ]] || { echo "ERROR: --repo needs a value" >&2; exit 2; }; REPO="$2"; shift 2 ;;
        --publish) PUBLISH=1; shift ;;
        *) echo "ERROR: unknown option $1" >&2; exit 2 ;;
    esac
done
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ && "$TAG" != *..* ]] || { echo "usage: $0 vX.Y.Z [--key PATH] [--repo owner/repo] [--publish]" >&2; exit 2; }
for t in gh ssh-keygen sha256sum; do command -v "$t" >/dev/null || { echo "ERROR: $t is required" >&2; exit 1; }; done
[[ -f "$KEY" ]] || { echo "ERROR: signing key not found: $KEY" >&2; exit 1; }

if [[ -z "$REPO" ]]; then
    url="$(git remote get-url origin)"
    REPO="$(sed -E 's#^(https://github\.com/|git@github\.com:)([^/]+/[^/.]+)(\.git)?/?$#\2#' <<<"$url")"
fi
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "ERROR: cannot determine the repository (use --repo owner/repo)" >&2; exit 1; }

SIGNERS=deploy/release-signers
if grep -q 'REEMPLAZAR-CON-TU-CLAVE-PUBLICA' "$SIGNERS"; then
    echo "ERROR: $SIGNERS still has the placeholder; put your public key there and run scripts/sync-signers.sh first" >&2
    exit 1
fi
scripts/sync-signers.sh --check

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> Downloading the draft assets of $TAG from $REPO..."
gh release download "$TAG" --repo "$REPO" --dir "$TMP" --pattern SHA256SUMS --pattern "proxmox-gui-${TAG}.tar.gz" --pattern install.sh

echo "==> Re-checking every hash in SHA256SUMS against the downloaded files..."
(cd "$TMP" && sha256sum -c SHA256SUMS) || { echo "ERROR: hash mismatch — the draft does not match its SHA256SUMS; NOT signing" >&2; exit 1; }
grep -q " proxmox-gui-${TAG}.tar.gz\$" "$TMP/SHA256SUMS" || { echo "ERROR: SHA256SUMS does not list the tarball" >&2; exit 1; }

echo "==> Signing SHA256SUMS (namespace proxmox-gui-release)..."
rm -f "$TMP/SHA256SUMS.sig"
ssh-keygen -Y sign -f "$KEY" -n proxmox-gui-release "$TMP/SHA256SUMS"

echo "==> Verifying the signature against $SIGNERS..."
ssh-keygen -Y verify -f <(grep -Ev '^[[:space:]]*(#|$)' "$SIGNERS") -I proxmox-gui-release -n proxmox-gui-release \
    -s "$TMP/SHA256SUMS.sig" <"$TMP/SHA256SUMS" >/dev/null \
    || { echo "ERROR: the signature does not verify with deploy/release-signers — wrong key? NOT uploading" >&2; exit 1; }

echo "==> Uploading SHA256SUMS.sig..."
gh release upload "$TAG" "$TMP/SHA256SUMS.sig" --repo "$REPO" --clobber
if [[ "$PUBLISH" -eq 1 ]]; then
    echo "==> Publishing $TAG..."
    gh release edit "$TAG" --repo "$REPO" --draft=false
fi
echo "Done. Verify an install with: bash install.sh --release $TAG"
