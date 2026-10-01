#!/usr/bin/env bash
# shellcheck shell=bash
#
# deploy/tests/smoke-systemd.sh — install the release into a Debian 12 container that runs a REAL systemd and
# check that the hardened stack actually starts and behaves (P7-02). "Real environment, no PVE":
#   - real apt, real pinned Node/Python downloads, real hash-locked pip install, real alembic
#   - real systemd units with their sandboxing (ProtectSystem=strict, NoNewPrivileges, capability bounding, ...)
#   - real Redis on a unix socket, real Caddy with the rendered Caddyfile, real path-activated updater unit
# It does NOT test: pct/pvesh/pveam, an unprivileged LXC's specifics, PVE itself, the SSH gate on a real node.
#
# Usage: deploy/tests/smoke-systemd.sh [--tarball FILE] [--logs DIR] [--keep]
#   --tarball FILE  release tarball built by scripts/build-release.sh (default: build v0.0.0-smoke here)
#   --logs DIR      where to write journals/analysis (default: ./smoke-logs)
#   --keep          leave the container running afterwards
# Needs: docker with a daemon (privileged containers, cgroup v2 host), network access.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
REPO_ROOT="$PWD"

TARBALL=""; LOGS="$REPO_ROOT/smoke-logs"; KEEP=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tarball) TARBALL="$2"; shift 2 ;;
        --logs) LOGS="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "the docker daemon is not reachable" >&2; exit 2; }

TAG="v0.0.0-smoke"
NAME="pgui-smoke-$$"
IMAGE="pgui-smoke-debian12"
mkdir -p "$LOGS"
WORK="$(mktemp -d)"
FAILED=0; PASSED=0

cleanup() {
    if [[ "$KEEP" -eq 0 ]]; then docker rm -f "$NAME" >/dev/null 2>&1 || true; fi
    rm -rf "$WORK"
}
trap cleanup EXIT

ct() { docker exec "$NAME" bash -c "$1"; }
ok()   { PASSED=$((PASSED + 1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAILED=$((FAILED + 1)); printf '  FAIL  %s\n' "$1" >&2; }
check() {  # check <description> <command inside the container>   (passes when the command exits 0)
    if ct "$2" >"$WORK/out" 2>&1; then ok "$1"; else bad "$1  [$(head -c 300 "$WORK/out" | tr '\n' ' ')]"; fi
}
check_not() {  # passes when the command FAILS (a forbidden action must not succeed)
    if ct "$2" >"$WORK/out" 2>&1; then bad "$1  [command unexpectedly succeeded]"; else ok "$1"; fi
}
check_eq() {  # check_eq <description> <expected> <command>
    local got; got="$(ct "$3" 2>&1 | tr -d '\r' | head -c 400)"
    if [[ "$got" == "$2" ]]; then ok "$1"; else bad "$1  [expected '$2', got '$got']"; fi
}
check_contains() {  # check_contains <description> <needle> <command>
    local got; got="$(ct "$3" 2>&1 | tr -d '\r')"
    if [[ "$got" == *"$2"* ]]; then ok "$1"; else bad "$1  [output lacks '$2': ${got:0:300}]"; fi
}

# ---------------------------------------------------------------------------
echo "==> Release tarball"
if [[ -z "$TARBALL" ]]; then
    echo "    building $TAG with scripts/build-release.sh ..."
    scripts/build-release.sh "$TAG" "$WORK/dist" >"$LOGS/build-release.log" 2>&1 || { tail -20 "$LOGS/build-release.log"; echo "release build failed" >&2; exit 1; }
    TARBALL="$WORK/dist/proxmox-gui-${TAG}.tar.gz"
fi
[[ -f "$TARBALL" ]] || { echo "tarball not found: $TARBALL" >&2; exit 2; }

echo "==> Debian 12 image with systemd"
docker build -q -t "$IMAGE" - >"$LOGS/docker-build.log" 2>&1 <<'DOCKERFILE' || { cat "$LOGS/docker-build.log"; exit 1; }
FROM debian:12
ENV container=docker DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv ca-certificates curl iproute2 procps \
    && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
DOCKERFILE

echo "==> Starting the container (real systemd as PID 1)"
docker run -d --name "$NAME" --privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
    state="$(docker exec "$NAME" systemctl is-system-running 2>/dev/null || true)"
    [[ "$state" == running || "$state" == degraded ]] && break
    sleep 1
done
echo "    systemd state: ${state:-unknown}"
[[ "${state:-}" == running || "${state:-}" == degraded ]] || { echo "systemd did not come up" >&2; exit 1; }

echo "==> Running bootstrap.sh from the release tarball (as install.sh does)"
ssh-keygen -t ed25519 -N "" -q -f "$WORK/signkey" -C smoke
printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "$(cut -d' ' -f1,2 "$WORK/signkey.pub")" >"$WORK/allowed_signers"
docker cp "$TARBALL" "$NAME:/root/pgui-release.tar.gz"
docker cp "$WORK/allowed_signers" "$NAME:/root/pgui-allowed-signers"
ct 'mkdir -p /root/pgui-src && tar -xzf /root/pgui-release.tar.gz -C /root/pgui-src --no-same-owner --no-same-permissions'
if ! docker exec -e PGUI_RELEASE_TAG="$TAG" -e PGUI_SRC_DIR=/root/pgui-src \
        -e PGUI_REPO_URL=https://github.com/dakser/proxmox-gui -e PGUI_SIGNERS_FILE=/root/pgui-allowed-signers \
        "$NAME" bash /root/pgui-src/deploy/lxc/bootstrap.sh >"$LOGS/bootstrap.log" 2>&1; then
    tail -30 "$LOGS/bootstrap.log"; echo "bootstrap failed" >&2
    ct 'journalctl --no-pager -n 80' >"$LOGS/journal-after-bootstrap-failure.log" 2>&1 || true
    # print the interesting bits so they are visible in the CI log without downloading the artifact
    # shellcheck disable=SC2016  # the command is meant to be expanded inside the container, not here
    ct 'systemctl --failed --no-pager; for u in $(systemctl --failed --plain --no-legend | awk "{print \$1}"); do echo "--- $u"; journalctl -u "$u" --no-pager -n 25; done' 2>&1 | tail -80
    exit 1
fi
tail -5 "$LOGS/bootstrap.log"
sleep 5   # let the API/worker/frontend settle

IP="$(ct "ip -4 -o addr show scope global | awk '{print \$4}' | cut -d/ -f1 | head -1")"
echo "==> Checks (container IP: ${IP:-?})"

echo "-- services"
for u in redis-server caddy proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend; do
    check "$u is active" "systemctl is-active --quiet $u"
done
check "updater path unit is active" "systemctl is-active --quiet proxmox-gui-updater.path"

echo "-- HTTP surface (through Caddy, TLS internal)"
check_contains "health answers over HTTPS on the container IP (the site name Caddy issued for)" '"status":"ok"' "curl -ksS --max-time 10 https://$IP/api/v1/health"
check_eq "a foreign Host header is refused by the API itself" "400" "curl -sS --max-time 10 -o /dev/null -w '%{http_code}' -H 'Host: evil.example' http://127.0.0.1:8000/api/v1/health"
check_not "a foreign Host header is not served the app through Caddy" "curl -ksS --max-time 10 -H 'Host: evil.example' https://$IP/api/v1/health | grep -q '\"status\":\"ok\"'"
check_eq "API docs are off (docs)" "404" "curl -ksS -o /dev/null -w '%{http_code}' https://$IP/api/docs"
check_eq "API docs are off (openapi.json)" "404" "curl -ksS -o /dev/null -w '%{http_code}' https://$IP/api/openapi.json"
# Before the wizard has run, /login redirects (303) to /setup; follow it to the page that is served.
check_eq "the frontend serves the UI (following the wizard redirect)" "200" "curl -ksS -L -o /dev/null -w '%{http_code}' https://$IP/login"
check_contains "CSP uses a nonce" "'nonce-" "curl -ksS -L -D - -o /dev/null https://$IP/login | tr -d '\r' | grep -i '^content-security-policy'"
check_not "CSP has no 'unsafe-inline' in script-src" "curl -ksS -L -D - -o /dev/null https://$IP/login | tr -d '\r' | grep -i '^content-security-policy' | grep -Eo \"script-src[^;]*\" | grep -q unsafe-inline"
check_contains "HSTS header present" "Strict-Transport-Security" "curl -ksS -L -D - -o /dev/null https://$IP/login | tr -d '\r'"
check_contains "API responses carry the locked-down CSP" "default-src 'none'" "curl -ksSI https://$IP/api/v1/health | tr -d '\r'"

echo "-- first-run wizard requires the setup token"
check_contains "status says a token is required" '"token_required":true' "curl -ksS https://$IP/api/v1/setup/status"
ADMIN='{"username":"smokeadmin","email":"smoke@example.com","password":"correct-horse-battery"}'
check_eq "no token -> 403" "403" "curl -ksS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '$ADMIN' https://$IP/api/v1/setup/admin"
check_eq "wrong token -> 403" "403" "curl -ksS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H 'X-Setup-Token: nope' -d '$ADMIN' https://$IP/api/v1/setup/admin"
check_eq "correct token -> 201" "201" "curl -ksS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H \"X-Setup-Token: \$(cat /etc/proxmox-gui/setup-token)\" -d '$ADMIN' https://$IP/api/v1/setup/admin"
check_eq "second attempt with the token -> 409" "409" "curl -ksS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H \"X-Setup-Token: \$(cat /etc/proxmox-gui/setup-token)\" -d '{\"username\":\"other1\",\"email\":\"o@example.com\",\"password\":\"correct-horse-battery\"}' https://$IP/api/v1/setup/admin"

echo "-- users, files and privileges"
check_eq "API runs as proxmox-gui" "proxmox-gui" "ps -o user= -p \$(systemctl show -p MainPID --value proxmox-gui-api)"
check_eq "worker runs as proxmox-gui" "proxmox-gui" "ps -o user= -p \$(systemctl show -p MainPID --value proxmox-gui-worker)"
check_eq "Node runs as proxmox-gui-web" "proxmox-gui-web" "ps -o user= -p \$(systemctl show -p MainPID --value proxmox-gui-frontend)"
for u in api worker frontend; do
    check_contains "$u: NoNewPrivs=1" "NoNewPrivs:	1" "grep NoNewPrivs /proc/\$(systemctl show -p MainPID --value proxmox-gui-$u)/status"
    check_contains "$u: no capabilities" "CapEff:	0000000000000000" "grep CapEff /proc/\$(systemctl show -p MainPID --value proxmox-gui-$u)/status"
done
check_not "the service user cannot modify the code tree" "runuser -u proxmox-gui -- touch /opt/proxmox-gui/releases/$TAG/backend/pwned"
check_not "the service user cannot add a release" "runuser -u proxmox-gui -- mkdir /opt/proxmox-gui/releases/evil"
check_not "the service user cannot replace a unit file" "runuser -u proxmox-gui -- sh -c 'echo x >> /etc/systemd/system/proxmox-gui-api.service'"
check_not "the service user cannot modify master.key" "runuser -u proxmox-gui -- sh -c 'echo x >> /etc/proxmox-gui/master.key'"
check "the service user can read master.key (needed at startup)" "runuser -u proxmox-gui -- test -r /etc/proxmox-gui/master.key"
check "the service user can write its data dir" "runuser -u proxmox-gui -- test -w /var/lib/proxmox-gui"
check_not "the web user cannot read /etc/proxmox-gui" "runuser -u proxmox-gui-web -- ls /etc/proxmox-gui"
check_not "the web user cannot read the database dir" "runuser -u proxmox-gui-web -- ls /var/lib/proxmox-gui"
check_not "the web user cannot use the Redis socket" "runuser -u proxmox-gui-web -- test -r /run/redis/redis-server.sock"
check_not "the service user cannot read the updater's work dir" "runuser -u proxmox-gui -- ls /var/lib/proxmox-gui-updater"
check_eq "code tree is owned by root" "0" "find /opt/proxmox-gui/releases -not -user root | wc -l"
check_eq "nothing in the code tree is group/other-writable" "0" "find /opt/proxmox-gui -perm /022 -not -type l | wc -l"
check_eq "master.key mode" "root:proxmox-gui 440" "stat -c '%U:%G %a' /etc/proxmox-gui/master.key"
check_eq "setup-token mode" "root:proxmox-gui 440" "stat -c '%U:%G %a' /etc/proxmox-gui/setup-token"
check_not "no sudo binary configured for the app" "test -e /etc/sudoers.d/proxmox-gui-systemctl"

echo "-- Redis"
check_not "Redis has no TCP listener" "ss -ltn | grep -E ':6379\\b'"
check "Redis unix socket exists" "test -S /run/redis/redis-server.sock"
check_contains "the service user reaches Redis over the socket" "PONG" "runuser -u proxmox-gui -- redis-cli -s /run/redis/redis-server.sock ping"

echo "-- updater (real .path activation, real unit sandbox)"
ct 'rm -f /run/proxmox-gui-updater/status.json'
ct "runuser -u proxmox-gui -- sh -c 'printf \"not-a-tag\" > /var/lib/proxmox-gui/update/request'"
sleep 8
check_contains "an invalid request is rejected by the updater" '"state":"failed"' "cat /run/proxmox-gui-updater/status.json"
check_not "the request file was consumed" "test -e /var/lib/proxmox-gui/update/request"
ct "runuser -u proxmox-gui -- sh -c 'printf \"$TAG\" > /var/lib/proxmox-gui/update/request'"
sleep 8
check_contains "requesting the installed version is a no-op" '"state":"noop"' "cat /run/proxmox-gui-updater/status.json"
check_eq "the active release did not change" "releases/$TAG" "readlink /opt/proxmox-gui/current"
ct "runuser -u proxmox-gui -- sh -c 'printf \"v9.9.9\" > /var/lib/proxmox-gui/update/request'"
sleep 10
check_contains "an unsigned/nonexistent newer release is refused (no download source)" '"state":"failed"' "cat /run/proxmox-gui-updater/status.json"
check_eq "and the active release still did not change" "releases/$TAG" "readlink /opt/proxmox-gui/current"
check_not "the updater left no half-installed release" "test -e /opt/proxmox-gui/releases/v9.9.9"

echo "-- Caddy and the rendered configuration"
check_contains "Caddyfile was rendered for the current IP" "https://$IP:443" "cat /etc/caddy/Caddyfile"
check_contains "host allow-list published for the API" "$IP" "cat /run/proxmox-gui/site.env"
ct 'systemctl restart caddy'; sleep 3
check "Caddy restarts and serves again (re-render)" "curl -ksS --max-time 10 https://$IP/api/v1/health | grep -q ok"

echo "-- journal hygiene"
for u in proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend; do
    check_eq "$u: no Python/Node tracebacks in the journal" "0" "journalctl -u $u --no-pager | grep -Eci 'traceback|uncaught|unhandled|EACCES|Permission denied'"
done

echo "-- systemd-analyze security (informational thresholds)"
for u in proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend; do
    score="$(ct "systemd-analyze security $u.service --no-pager 2>/dev/null | tail -1" | tr -d '\r')"
    echo "    $u: $score"
    echo "$u: $score" >>"$LOGS/systemd-analyze-summary.txt"
    exposure="$(sed -nE 's/.*: ([0-9]+\.[0-9]+) .*/\1/p' <<<"$score")"
    if [[ -n "$exposure" ]] && awk "BEGIN{exit !($exposure <= 3.0)}"; then ok "$u exposure level $exposure <= 3.0"; else bad "$u exposure level '${exposure:-unknown}' > 3.0"; fi
done

# ---------------------------------------------------------------------------
echo "==> Collecting logs into $LOGS"
for u in redis-server caddy proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend proxmox-gui-updater proxmox-gui-caddy-render; do
    ct "journalctl -u $u --no-pager" >"$LOGS/journal-$u.log" 2>&1 || true
done
for u in proxmox-gui-api proxmox-gui-worker proxmox-gui-frontend proxmox-gui-updater; do
    ct "systemd-analyze security $u.service --no-pager" >"$LOGS/systemd-analyze-$u.txt" 2>&1 || true
done
ct 'cat /run/proxmox-gui-updater/status.json; echo; ls -la /opt/proxmox-gui /etc/proxmox-gui /var/lib/proxmox-gui /var/lib/proxmox-gui-updater' >"$LOGS/state.txt" 2>&1 || true

echo
echo "smoke-systemd: $PASSED passed, $FAILED failed"
[[ "$FAILED" -eq 0 ]]
