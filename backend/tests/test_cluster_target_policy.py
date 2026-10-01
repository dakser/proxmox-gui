"""Cluster registration target policy (P5-06, F-13): no SSRF towards loopback/link-local/metadata,
sane ports, bounded/validated fields, no reflection of PVE error bodies."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.clusters import target_policy
from app.clusters.schemas import ClusterCreate, ClusterTestRequest, ClusterUpdate


def _payload(**over):
    base = dict(name="c", host="pve.example.test", port=8006, verify_ssl=False, token_user="gui@pve",
                token_name="t", api_token_secret="s")
    base.update(over)
    return base


BAD_HOSTS = [
    "127.0.0.1", "127.1.2.3", "localhost", "LOCALHOST", "ip6-localhost", "0.0.0.0", "::1", "[::1]", "::",
    "169.254.169.254", "169.254.0.1", "fe80::1", "fd00:ec2::254", "224.0.0.1", "255.255.255.255",
    "metadata.google.internal", "foo.localhost", "10.0.0.1/x", "host name", "a@b", "a?b", "a#b", "a:b:c",
    "http://x", "x/y", "a\\b", "", " ", "pve\nhost", "-bad.example", "bad-.example", "a..b", "x" * 300,
    "::ffff:127.0.0.1", "0177.0.0.1", "2130706433", "0x7f.0.0.1", "127.0.0.1.", "1.2.3",
]


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_forbidden_or_malformed_hosts_are_rejected_by_every_schema(host):
    for schema in (ClusterCreate, ClusterTestRequest):
        payload = _payload(host=host)
        if schema is ClusterTestRequest:
            payload.pop("name")
        with pytest.raises(ValidationError):
            schema(**payload)
    with pytest.raises(ValidationError):
        ClusterUpdate(host=host)


@pytest.mark.parametrize("host", ["pve.example.test", "pve1", "10.0.0.5", "192.168.1.10", "172.16.0.9", "8.8.8.8",
                                  "2001:db8::10", "pve-01.lab.example.org"])
def test_normal_pve_hosts_are_accepted(host):
    assert ClusterCreate(**_payload(host=host)).host == host


@pytest.mark.parametrize("port", [22, 25, 53, 80, 110, 389, 445, 1023, 3306, 6379, 1])
def test_low_and_service_ports_are_rejected(port):
    with pytest.raises(ValidationError):
        ClusterCreate(**_payload(port=port))


@pytest.mark.parametrize("port", [443, 8006, 1024, 8443, 65535])
def test_reasonable_ports_are_accepted(port):
    assert ClusterCreate(**_payload(port=port)).port == port


@pytest.mark.parametrize("fp", ["zz" * 32, "ab" * 31, "ab" * 33, "ab:cd", "'; drop", "a" * 200, "ab" * 32 + "\n"])
def test_bad_fingerprints_are_rejected(fp):
    with pytest.raises(ValidationError):
        ClusterCreate(**_payload(tls_fingerprint=fp))


@pytest.mark.parametrize("fp", ["ab" * 32, ":".join(["AB"] * 32), "0" * 64])
def test_good_fingerprints_are_accepted(fp):
    assert ClusterCreate(**_payload(tls_fingerprint=fp)).tls_fingerprint == fp


def test_text_fields_are_bounded():
    with pytest.raises(ValidationError):
        ClusterCreate(**_payload(notes="x" * 5000))
    with pytest.raises(ValidationError):
        ClusterCreate(**_payload(name="n" * 200))
    with pytest.raises(ValidationError):
        ClusterCreate(**_payload(name="bad\x00name"))


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", ["127.0.0.1", "169.254.169.254", "::1", "0.0.0.0", "fe80::1"])
async def test_a_hostname_resolving_to_a_forbidden_address_is_refused(monkeypatch, resolved):
    import socket

    def fake_getaddrinfo(host, port, *a, **k):
        fam = socket.AF_INET6 if ":" in resolved else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (resolved, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(target_policy.TargetNotAllowed):
        await target_policy.check_target("pve.rebind.example", 8006)


@pytest.mark.asyncio
async def test_a_hostname_with_any_forbidden_record_is_refused(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", p)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", p)),
    ])
    with pytest.raises(target_policy.TargetNotAllowed):
        await target_policy.check_target("pve.example.test", 8006)


@pytest.mark.asyncio
async def test_private_and_unresolvable_targets_are_allowed(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", p))])
    await target_policy.check_target("pve.lan.example", 8006)
    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    await target_policy.check_target("does-not-resolve.example", 8006)  # the connect itself will fail later


@pytest.mark.asyncio
async def test_test_endpoint_and_register_refuse_a_rebinding_host(client, session_factory, monkeypatch):
    import socket

    from tests.factories import login_as, make_user

    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", p))])
    await make_user(session_factory, username="ssrfadmin", is_admin=True, password="testpass12345")
    cookies = await login_as(client, username="ssrfadmin", password="testpass12345")
    hdr = {"X-CSRF-Token": cookies["csrf_token"]}
    body = _payload(host="rebind.example.test")
    t = await client.post("/api/v1/clusters/test", json={k: v for k, v in body.items() if k != "name"},
                          cookies=cookies, headers=hdr)
    assert t.status_code in (200, 422)
    if t.status_code == 200:
        assert t.json()["ok"] is False
    r = await client.post("/api/v1/clusters/", json=body, cookies=cookies, headers=hdr)
    assert r.status_code == 422
    assert "169.254" not in r.text  # the resolved address is not echoed back


@pytest.mark.asyncio
async def test_bootstrap_failure_does_not_reflect_the_pve_error_body(client, session_factory):
    from app.main import create_app  # noqa: F401
    from app.teams.bootstrap import BootstrapFailed

    app = client._transport.app  # type: ignore[attr-defined]
    handler = app.exception_handlers[BootstrapFailed]
    evil = RuntimeError("<script>alert(1)</script> 500 Internal: /etc/pve/priv/token.cfg secret=abcdef")
    resp = await handler(None, BootstrapFailed(cluster_name="prod", original=evil))
    text = resp.body.decode()
    assert "prod" in text
    assert "script" not in text and "token.cfg" not in text and "abcdef" not in text
