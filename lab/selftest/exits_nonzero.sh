#!/usr/bin/env bash
# shellcheck shell=bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
lab_result exits_nonzero pass=true
exit 3
