#!/usr/bin/env bash
# shellcheck shell=bash
# Build the release tarball + checksums + SBOMs. Used by .github/workflows/release.yml and runnable locally.
# Usage: scripts/build-release.sh vX.Y.Z [output-dir]      (default output dir: ./dist)
#
# Output (all unsigned; signing is done locally with scripts/release-sign.sh, D3):
#   proxmox-gui-<tag>.tar.gz   flat layout: backend/ frontend/ deploy/ VERSION  (no top directory)
#   SHA256SUMS                 sha256 of the tarball (the file that gets signed)
#   sbom-backend.cdx.json, sbom-frontend.cdx.json   CycloneDX SBOMs (when the tools are available)
#
# The tarball is deterministic (sorted names, mtime 0, owner 0, gzip -n) and contains only regular
# files and directories (the updater refuses anything else). The frontend is compiled here from
# frontend/src with the frozen lockfile; production node_modules are installed hoisted (no symlinks,
# no lifecycle scripts) so `node frontend/build/index.js` runs without a network or a package manager.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$PWD"

TAG="${1:?usage: scripts/build-release.sh vX.Y.Z [output-dir]}"
OUT="${2:-dist}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ && "$TAG" != *..* ]] || { echo "ERROR: bad tag '$TAG'" >&2; exit 1; }
[[ ! -f deploy/pins.env ]] || ! grep -q 'TODO-PIN' deploy/pins.env || { echo "ERROR: deploy/pins.env has TODO-PIN" >&2; exit 1; }
[[ -s backend/requirements.lock ]] || { echo "ERROR: backend/requirements.lock missing" >&2; exit 1; }

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Building the frontend from source (frozen lockfile)..."
(cd frontend && pnpm install --frozen-lockfile && pnpm build)

echo "==> Staging the release tree..."
mkdir -p "$STAGE/backend" "$STAGE/deploy" "$STAGE/frontend"
tar -C backend --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='build' --exclude='dist' \
    --exclude='*.egg-info' --exclude='tests' --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
    --exclude='*.db' -cf - . | tar -C "$STAGE/backend" -xf -
tar -C deploy --exclude='tests' -cf - . | tar -C "$STAGE/deploy" -xf -
cp -r frontend/build "$STAGE/frontend/build"
cp frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml "$STAGE/frontend/"

echo "==> Installing production frontend dependencies (hoisted, no scripts)..."
# Remove the copied build's own node_modules if a stale one exists (adapter-node output can vary by version).
rm -rf "$STAGE/frontend/build/node_modules"
(cd "$STAGE/frontend" && pnpm install --prod --frozen-lockfile --node-linker=hoisted --ignore-scripts)
find "$STAGE/frontend/node_modules" -name .bin -type d -prune -exec rm -rf {} +
rm -f "$STAGE/frontend/pnpm-workspace.yaml"

printf '%s\n' "$TAG" >"$STAGE/VERSION"

echo "==> Checking the tree (regular files and directories only)..."
if find "$STAGE" ! -type f ! -type d | grep -q .; then
    echo "ERROR: non-regular files in the release tree:" >&2
    find "$STAGE" ! -type f ! -type d | head >&2
    exit 1
fi
[[ -f "$STAGE/frontend/build/index.js" ]] || { echo "ERROR: frontend/build/index.js missing" >&2; exit 1; }
[[ -f "$STAGE/frontend/node_modules/@sveltejs/kit/package.json" ]] || { echo "ERROR: production node_modules incomplete" >&2; exit 1; }
chmod -R u=rwX,go=rX "$STAGE"

echo "==> Packing (deterministic)..."
NAME="proxmox-gui-${TAG}.tar.gz"
(cd "$STAGE" && tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -cf - backend deploy frontend VERSION) \
    | gzip -n -9 >"$OUT/$NAME"
(cd "$OUT" && sha256sum "$NAME" >SHA256SUMS)

echo "==> SBOMs..."
if command -v cyclonedx-py >/dev/null 2>&1; then
    cyclonedx-py requirements "$REPO_ROOT/backend/requirements.lock" --of JSON -o "$OUT/sbom-backend.cdx.json" >/dev/null
else
    echo "WARN: cyclonedx-py not installed; backend SBOM skipped" >&2
fi
(cd frontend && pnpm sbom --sbom-format cyclonedx --prod >"$OUT/sbom-frontend.cdx.json") \
    || echo "WARN: pnpm sbom failed; frontend SBOM skipped" >&2

echo "==> Done:"
ls -l "$OUT"
cat "$OUT/SHA256SUMS"
