#!/usr/bin/env bash
# shellcheck shell=bash
# Common body for the fake host binaries. Each shim is a symlink-free tiny
# wrapper: `exec "$(dirname "$0")/_shim.sh" <name> "$@"` semantics, implemented
# by sourcing this file with SHIM_NAME set. Behaviour comes from
# "$SHIM_CFG/$SHIM_NAME.sh" (defines handle()); default is silent success.
: "${SHIM_LOG:?SHIM_LOG not set (run through deploy/tests/run.sh)}"
{
    printf '%s' "$SHIM_NAME"
    for a in "$@"; do printf ' %q' "$a"; done
    printf '\n'
} >>"$SHIM_LOG"
if [[ -n "${SHIM_CFG:-}" && -f "$SHIM_CFG/$SHIM_NAME.sh" ]]; then
    # shellcheck disable=SC1090
    source "$SHIM_CFG/$SHIM_NAME.sh"
    handle "$@"
    exit $?
fi
exit 0
