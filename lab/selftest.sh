#!/usr/bin/env bash
# shellcheck shell=bash
# Autoprueba del ejecutor de laboratorio: no necesita Proxmox. El ejecutor es la regla de medir; si falla en silencio,
# todas las puertas de laboratorio mienten. Sale con 0 solo si TODAS las comprobaciones se cumplen.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PGUI_LAB_RESULTS="$tmp/res"
fail=0
check() { # check <descripción> <esperado: pass|fail> <nombre>
    local desc="$1" want="$2" name="$3" got=fail
    lab/run.sh "$name" lab/selftest >/dev/null 2>&1 && got=pass
    if [[ "$got" == "$want" ]]; then echo "ok   $desc"; else echo "FALLA $desc (esperado $want, fue $got)"; fail=1; fi
}
check "escenario correcto con informe pass=true" pass ok
check "escenario que sale con código distinto de 0 falla aunque el informe diga pass" fail exits_nonzero
check "informe con pass=false falla" fail pass_false
check "sin informe falla" fail no_report
check "escenario inexistente falla" fail does_not_exist
# Un informe viejo nunca cuenta: lo dejamos con pass=true y el escenario no lo reescribe.
mkdir -p "$PGUI_LAB_RESULTS"
echo '{"pass": true}' >"$PGUI_LAB_RESULTS/no_report.json"
check "un informe viejo no cuenta" fail no_report
# La limpieza corre, en orden inverso, aunque el escenario falle.
lab/run.sh cleanup_on_fail lab/selftest >/dev/null 2>&1
if [[ -f "$PGUI_LAB_RESULTS/cleaned-1" && -f "$PGUI_LAB_RESULTS/cleaned-2" ]]; then echo "ok   la limpieza corre al fallar"; else echo "FALLA la limpieza no corrió"; fail=1; fi
# El nombre se valida (no se puede salir del directorio).
lab/run.sh '../x' lab/selftest >/dev/null 2>&1 && { echo "FALLA acepta nombres con ruta"; fail=1; } || echo "ok   rechaza nombres con ruta"
# lab_load_env rechaza un archivo legible por otros y no imprime su contenido.
printf 'PGUI_LAB_PVE_TOKEN_SECRET=supersecreto\n' >"$tmp/env"
chmod 644 "$tmp/env"
out="$(PGUI_LAB_ENV_FILE="$tmp/env" bash -c 'source lab/lib.sh; lab_load_env' 2>&1)" && { echo "FALLA acepta env con modo 644"; fail=1; } || echo "ok   rechaza env con permisos laxos"
chmod 600 "$tmp/env"
out="$(PGUI_LAB_ENV_FILE="$tmp/env" bash -c 'source lab/lib.sh; lab_load_env' 2>&1)"
if grep -q supersecreto <<<"$out"; then echo "FALLA imprime el secreto"; fail=1; else echo "ok   no imprime el secreto"; fi
lab_json="$(PGUI_LAB_RESULTS="$tmp/t" bash -c 'source lab/lib.sh; lab_result t pass=true n=7 x=0.5 s=hola; cat "$PGUI_LAB_RESULTS/t.json"')"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d=={"pass":True,"n":7,"x":0.5,"s":"hola"}, d' "$lab_json" && echo "ok   lab_result escribe tipos JSON correctos" || { echo "FALLA tipos de lab_result"; fail=1; }
# Resultado para la puerta
mkdir -p reports/lab
python3 -c "import json,sys; json.dump({'pass': $([[ $fail -eq 0 ]] && echo True || echo False)}, open('reports/lab/selftest.json','w'))"
exit "$fail"
