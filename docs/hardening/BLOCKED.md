# Bloqueos

## P7-02 — la prueba de humo no se pudo ejecutar en la sesión de Claude
- Intentado: arrancar `dockerd` en el sandbox (funciona) y `docker pull debian:12` (funciona); `docker build` de la imagen con systemd falla en `apt-get update`
  porque la política de red de la sesión bloquea `deb.debian.org` (HTTP 403 del proxy de salida; no se elude).
- Consecuencia: `deploy/tests/smoke-systemd.sh` solo se ejecuta en GitHub Actions (job `smoke-systemd`, runner con red completa); su resultado se revisa allí.
