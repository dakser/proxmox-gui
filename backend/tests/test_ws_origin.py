"""WebSocket Origin validation (P5-03, F-12): the session cookie must not authenticate a socket
opened from another site (cross-site WebSocket hijacking)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from tests.test_console import _ws_test_app

PATHS = ["/api/v1/ws/jobs", "/api/v1/ws/console/1/vms/100"]


def _closed_code(app, path, headers):
    with TestClient(app) as tc:
        try:
            with tc.websocket_connect(path, headers=headers) as ws:
                ws.receive_text()
        except WebSocketDisconnect as exc:
            return exc.code
    return None


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "headers",
    [
        {},  # no Origin at all
        {"origin": "https://evil.example"},
        {"origin": "http://testserver.evil.example"},
        {"origin": "http://evil.example/testserver"},
        {"origin": "null"},
        {"origin": "file://testserver"},
        {"origin": "http://"},
        {"origin": "javascript://testserver"},
        {"origin": "http://testserver:8080"},  # different port = different origin/host
    ],
)
def test_foreign_or_missing_origin_is_closed_1008_before_accept(session_factory, path, headers):
    assert _closed_code(_ws_test_app(session_factory), path, headers) == 1008


def test_same_origin_passes_the_origin_check_and_hits_auth(session_factory):
    """Correct Origin gets past the guard; it is then rejected only for the missing session (1008),
    and the log line proves which check fired."""
    import logging

    records: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = H()
    lg = logging.getLogger("app.jobs.ws")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        assert _closed_code(_ws_test_app(session_factory), "/api/v1/ws/jobs", {"origin": "http://testserver"}) == 1008
        assert not any("Origin" in r for r in records)
        records.clear()
        assert _closed_code(_ws_test_app(session_factory), "/api/v1/ws/jobs", {"origin": "https://evil.example"}) == 1008
        assert any("Origin" in r for r in records)
    finally:
        lg.removeHandler(h)


def test_allowed_origins_setting_admits_a_listed_origin(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "allowed_origins", ["https://gui.example.org"])
    from starlette.requests import HTTPConnection  # noqa: F401

    from app.jobs.ws import origin_allowed

    class WS:
        def __init__(self, origin, host):
            self.headers = {k: v for k, v in (("origin", origin), ("host", host)) if v}

    assert origin_allowed(WS("https://gui.example.org", "10.0.0.5")) is True
    assert origin_allowed(WS("https://other.example.org", "10.0.0.5")) is False
    assert origin_allowed(WS("https://10.0.0.5", "10.0.0.5")) is True   # same host, scheme-agnostic
    assert origin_allowed(WS(None, "10.0.0.5")) is False
