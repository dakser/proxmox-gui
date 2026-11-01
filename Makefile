# Makefile del kit. Nombres de objetivo fijos: los usan gates.toml y el orquestador. Solo el humano lo cambia.
.PHONY: check lab gate lock-check accept selftest

# check: verificación completa del proyecto (scripts/check.sh) + autopruebas del kit.
check: selftest
	scripts/check.sh

# selftest: las autopruebas del kit (ejecutor de laboratorio, verificador de manifiesto, guardas de agentes).
selftest:
	lab/selftest.sh
	python3 scripts/accept_check_selftest.py
	python3 scripts/gate_selftest.py
	python3 scripts/guard_selftest.py

# lab: corre un escenario de laboratorio de nivel 1.   make lab SCN=f1_new_user_can_provision
lab:
	@if [ -z "$(SCN)" ]; then echo "Usa: make lab SCN=f1_new_user_can_provision"; exit 1; fi
	lab/run.sh $(SCN)

# gate: puerta automática de una fase.   make gate PHASE=F1
gate:
	@if [ -z "$(PHASE)" ]; then echo "Usa: make gate PHASE=F1"; exit 1; fi
	python3 scripts/gate.py $(PHASE)

# lock-check: separación entre pruebas de aceptación, código y kit.
lock-check:
	python3 scripts/lock_check.py master

# accept: las pruebas del manifiesto existen.   make accept PHASE=F1
accept:
	@if [ -z "$(PHASE)" ]; then echo "Usa: make accept PHASE=F1"; exit 1; fi
	python3 scripts/accept_check.py $(PHASE)
