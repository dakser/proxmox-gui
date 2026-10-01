#!/usr/bin/env bash
# shellcheck shell=bash
# Recompute deploy/pins.env for a Node 22 LTS release and a python-build-standalone release.
# Usage: scripts/update-pins.sh <node-version e.g. 22.23.3> <pbs-release e.g. 20260929>
# Needs network access to nodejs.org and github.com. The script prints what it pins;
# review the diff before committing. Hashes come from the publishers' checksum files
# AND are re-computed from the downloaded archives (both must agree).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
NODE_V="${1:?usage: update-pins.sh <node-version> <pbs-release>}"
PBS_REL="${2:?usage: update-pins.sh <node-version> <pbs-release>}"
[[ "$NODE_V" =~ ^22\.[0-9]+\.[0-9]+$ ]] || { echo "ERROR: node version must be 22.x.y" >&2; exit 1; }
[[ "$PBS_REL" =~ ^[0-9]{8}$ ]] || { echo "ERROR: pbs release must look like 20260929" >&2; exit 1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
dl() { curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$2" "$1"; }

node_file="node-v${NODE_V}-linux-x64.tar.xz"
node_url="https://nodejs.org/dist/v${NODE_V}/${node_file}"
dl "https://nodejs.org/dist/v${NODE_V}/SHASUMS256.txt" "$tmp/node.sums"
node_pub="$(awk -v f="$node_file" '$2 == f {print $1}' "$tmp/node.sums")"
dl "$node_url" "$tmp/node.tar.xz"
node_sha="$(sha256sum "$tmp/node.tar.xz" | awk '{print $1}')"
[[ -n "$node_pub" && "$node_pub" == "$node_sha" ]] || { echo "ERROR: node checksum mismatch ($node_pub vs $node_sha)" >&2; exit 1; }

dl "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_REL}/SHA256SUMS" "$tmp/pbs.sums"
py_line="$(grep -E "cpython-3\.12\.[0-9]+\+${PBS_REL}-x86_64-unknown-linux-gnu-install_only_stripped\.tar\.gz\$" "$tmp/pbs.sums" | head -1)"
[[ -n "$py_line" ]] || { echo "ERROR: no CPython 3.12 x86_64 linux install_only_stripped asset in release $PBS_REL" >&2; exit 1; }
py_pub="${py_line%% *}"; py_file="${py_line##* }"
py_ver="$(sed -E 's/^cpython-(3\.12\.[0-9]+)\+.*/\1/' <<<"$py_file")"
py_url="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_REL}/${py_file//+/%2B}"
dl "$py_url" "$tmp/py.tar.gz"
py_sha="$(sha256sum "$tmp/py.tar.gz" | awk '{print $1}')"
[[ "$py_pub" == "$py_sha" ]] || { echo "ERROR: python checksum mismatch" >&2; exit 1; }

cat >deploy/pins.env <<EOF
# Pinned toolchain downloads (P3-05). bootstrap.sh downloads each URL to a file,
# verifies SHA-256 with \`sha256sum -c\`, and only then extracts it. Linux x86_64
# (amd64 Debian 12 LXC) only. Refresh with scripts/update-pins.sh.
NODE_VERSION=${NODE_V}
NODE_URL=${node_url}
NODE_SHA256=${node_sha}
PYTHON_VERSION=${py_ver}
PYTHON_URL=${py_url}
PYTHON_SHA256=${py_sha}
EOF
echo "pinned node ${NODE_V} (${node_sha}) and python ${py_ver} (${py_sha})"
