#!/usr/bin/env bash
# shellcheck shell=bash
# Biblioteca de los escenarios de laboratorio (nivel 1). Se carga con `source lab/lib.sh`.
#
# Contrato de un escenario (lab/scenarios/<nombre>.sh):
#   * sale con 0 solo si cumple;
#   * escribe ${PGUI_LAB_RESULTS:-reports/lab}/<nombre>.json con al menos {"pass": bool} y las claves que lea su puerta
#     (use `lab_result`);
#   * registra con `lab_on_exit` cada cosa que cree en Proxmox o en el portal, para que se limpie aunque falle.
# `lab/run.sh` borra el informe antes de correr, así que un informe viejo nunca cuenta.
# Nunca imprimir secretos: `lab_load_env` no los muestra y los escenarios no deben hacer `set -x`.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_RESULTS="${PGUI_LAB_RESULTS:-$LAB_ROOT/reports/lab}"
LAB_ENV_FILE="${PGUI_LAB_ENV_FILE:-$HOME/.pgui-lab.env}"
_LAB_CLEANUPS=()

lab_log() { printf '[lab] %s\n' "$*" >&2; }

# Carga ~/.pgui-lab.env (modo 0600, fuera del repositorio). Falla si no existe o es legible por otros.
lab_load_env() {
    [[ -f "$LAB_ENV_FILE" ]] || { lab_log "falta $LAB_ENV_FILE (ver docs/hardening/BRIEF-OPENCODE.md §1)"; return 1; }
    local mode
    mode="$(stat -c '%a' "$LAB_ENV_FILE")"
    [[ "$mode" == "600" || "$mode" == "400" ]] || { lab_log "$LAB_ENV_FILE debe tener modo 0600 (tiene $mode)"; return 1; }
    set -a
    # shellcheck disable=SC1090
    source "$LAB_ENV_FILE"
    set +a
}

# Registra una orden de limpieza (se ejecutan en orden inverso, al salir, pase lo que pase).
lab_on_exit() { _LAB_CLEANUPS+=("$1"); }

_lab_run_cleanups() {
    local rc=$? i
    trap - EXIT
    for ((i = ${#_LAB_CLEANUPS[@]} - 1; i >= 0; i--)); do
        eval "${_LAB_CLEANUPS[i]}" || lab_log "la limpieza falló: ${_LAB_CLEANUPS[i]}"
    done
    exit "$rc"
}
trap _lab_run_cleanups EXIT

# lab_result <nombre> <clave=valor>...   Valores: true/false/número/texto. Escribe el JSON del escenario.
lab_result() {
    local name="$1"
    shift
    mkdir -p "$LAB_RESULTS"
    python3 - "$LAB_RESULTS/$name.json" "$@" <<'PY'
import json, sys
out, doc = sys.argv[1], {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("=")
    doc[k] = {"true": True, "false": False}.get(v, v)
    if isinstance(doc[k], str):
        try:
            doc[k] = int(v)
        except ValueError:
            try:
                doc[k] = float(v)
            except ValueError:
                pass
with open(out, "w") as f:
    json.dump(doc, f, ensure_ascii=False)
    f.write("\n")
PY
}

# Llamada a la API de Proxmox del laboratorio con el token del agente. No imprime el secreto.
# lab_pve GET /nodes   |   lab_pve POST /pools poolid=x
lab_pve() {
    local method="$1" path="$2"
    shift 2
    local args=()
    local kv
    for kv in "$@"; do args+=(--data-urlencode "$kv"); done
    curl -ksS -X "$method" -H "Authorization: PVEAPIToken=${PGUI_LAB_PVE_TOKEN_ID}=${PGUI_LAB_PVE_TOKEN_SECRET}" \
        "${args[@]}" "${PGUI_LAB_PVE_URL}/api2/json${path}"
}
