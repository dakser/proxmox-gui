#!/usr/bin/env bash
# shellcheck shell=bash
# Ejecuta un escenario de laboratorio y comprueba su veredicto.   Uso: lab/run.sh <nombre> [dir_de_escenarios]
# Sale con 0 solo si el escenario salió con 0 Y su informe existe, es JSON y trae "pass": true.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
name="${1:-}"
dir="${2:-lab/scenarios}"
[[ "$name" =~ ^[A-Za-z0-9_]+$ ]] || { echo "uso: lab/run.sh <nombre> (letras, números y _)" >&2; exit 2; }
script="$dir/$name.sh"
[[ -f "$script" ]] || { echo "lab: no existe $script" >&2; exit 2; }
results="${PGUI_LAB_RESULTS:-reports/lab}"
mkdir -p "$results"
rm -f "$results/$name.json"
PGUI_LAB_RESULTS="$results" bash "$script"
rc=$?
if [[ "$rc" -ne 0 ]]; then
    echo "lab: el escenario $name salió con $rc" >&2
    exit 1
fi
python3 - "$results/$name.json" <<'PY' || exit 1
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"lab: informe ausente o inválido: {e}", file=sys.stderr); sys.exit(1)
if doc.get("pass") is not True:
    print("lab: el informe no trae pass=true", file=sys.stderr); sys.exit(1)
PY
echo "lab: $name OK"
