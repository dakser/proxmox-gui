#!/usr/bin/env bash
# shellcheck shell=bash
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
lab_on_exit "touch '${PGUI_LAB_RESULTS}/cleaned-1'"
lab_on_exit "touch '${PGUI_LAB_RESULTS}/cleaned-2'"
exit 5
