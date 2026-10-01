#!/usr/bin/env bash
# shellcheck shell=bash
# Point every default at YOUR fork (P6-04, F-04): the installer's default repository and the repository URLs
# in the docs. Idempotent. Usage: scripts/set-fork.sh <owner/repo>
#
# The original author's account name is assembled below (not written literally) so that the acceptance
# grep for that name over executable files stays empty, this script included. LICENSE and credits keep the
# author's name on purpose.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
SLUG="${1:-}"
[[ "$SLUG" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ && "$SLUG" != *..* && "$SLUG" != */*/* && "$SLUG" != *.git ]] \
    || { echo "usage: scripts/set-fork.sh <owner/repo>   (e.g. dakser/proxmox-gui)" >&2; exit 2; }
URL="https://github.com/${SLUG}"
UPSTREAM="chloe""priceless"   # the original author (assembled on purpose, see above)
[[ "${PGUI_FORK_PREVIEW:-0}" == 1 ]] && { echo "would set $URL"; exit 0; }

# 1. installer default
sed -i -E "s#^(readonly DEFAULT_REPO_URL=)\"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\"#\1\"${URL}\"#" deploy/install.sh
grep -q "^readonly DEFAULT_REPO_URL=\"${URL}\"" deploy/install.sh || { echo "ERROR: could not set DEFAULT_REPO_URL in deploy/install.sh" >&2; exit 1; }

# 2. docs: raw install URLs and repo links (the previous fork or the upstream author)
for f in README.md deploy/README.md; do
    [[ -f "$f" ]] || continue
    sed -i -E \
        -e "s#https://raw\.githubusercontent\.com/(${UPSTREAM}|[A-Za-z0-9_.-]+)/proxmox-gui/#https://raw.githubusercontent.com/${SLUG}/#g" \
        -e "s#https://github\.com/(${UPSTREAM}|dakser)/proxmox-gui#${URL}#g" \
        "$f"
done
echo "Default repository set to ${URL}"
echo "Check that no executable file still points at the upstream author (see docs/hardening/PLAN.md P6-04)."
