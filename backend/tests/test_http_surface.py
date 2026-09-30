"""HTTP surface hardening (P5-04, F-12): docs off by default (D11), Host allow-list, and the
X-Forwarded-For trust rule."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings


async def _client(monkeypatch, **overrides):
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)
    from app.main import create_app

    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc", "/api/openapi.json", "/api/docs/oauth2-redirect"])
async def test_docs_and_schema_are_off_by_default(monkeypatch, path):
    assert settings.enable_docs is False
    async with await _client(monkeypatch) as c:
        assert (await c.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_docs_can_be_enabled_explicitly(monkeypatch):
    async with await _client(monkeypatch, enable_docs=True) as c:
        assert (await c.get("/api/openapi.json")).status_code == 200
        assert (await c.get("/api/docs")).status_code == 200


def test_enable_docs_reads_the_documented_env_var(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("PROXMOX_GUI_ENABLE_DOCS", "true")
    assert Settings(jwt_secret="x", pat_pepper="y").enable_docs is True
    monkeypatch.delenv("PROXMOX_GUI_ENABLE_DOCS")
    assert Settings(jwt_secret="x", pat_pepper="y").enable_docs is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "code"),
    [
        ("10.0.0.5", 200),
        ("10.0.0.5:443", 200),
        ("gui.example.org", 200),
        ("localhost", 200),
        ("evil.example", 400),
        ("10.0.0.5.evil.example", 400),
        ("gui.example.org.evil.example", 400),
        ("evil.example/10.0.0.5", 400),
    ],
)
async def test_trusted_host_allow_list(monkeypatch, host, code):
    async with await _client(monkeypatch, allowed_hosts=["10.0.0.5", "gui.example.org", "localhost"]) as c:
        r = await c.get("/api/v1/health", headers={"Host": host})
        assert r.status_code == code


@pytest.mark.asyncio
async def test_host_check_is_not_enforced_when_unset(monkeypatch):
    async with await _client(monkeypatch, allowed_hosts=[]) as c:
        assert (await c.get("/api/v1/health", headers={"Host": "anything.example"})).status_code == 200


def _req(client_host, xff=None):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (client_host, 1234) if client_host else None})


def test_x_forwarded_for_only_trusted_from_the_local_proxy():
    from app.core.source_ip import extract_source_ip

    # Caddy (loopback) may vouch for the client address ...
    assert extract_source_ip(_req("127.0.0.1", "203.0.113.9")) == "203.0.113.9"
    assert extract_source_ip(_req("::1", "203.0.113.9, 10.0.0.1")) == "203.0.113.9"
    # ... nobody else can: a direct peer's header is ignored (no audit/rate-limit spoofing).
    assert extract_source_ip(_req("198.51.100.7", "203.0.113.9")) == "198.51.100.7"
    assert extract_source_ip(_req("10.0.0.99", "127.0.0.1")) == "10.0.0.99"
    assert extract_source_ip(_req(None, "1.2.3.4")) is None
