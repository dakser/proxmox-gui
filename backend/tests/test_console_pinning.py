"""Console relay pins the PVE certificate (P5-05, F-13): with verify_ssl=False and a stored
fingerprint the upstream wss leg is dropped on mismatch BEFORE the API token is sent."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.console import proxy


def _make_cert(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    crt, k = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    return crt, k, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class _Connector:
    def __init__(self, verify_ssl, tls_fingerprint):
        self.verify_ssl = verify_ssl
        self.tls_fingerprint = tls_fingerprint


async def _server(crt, key, seen):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)

    async def handler(ws):
        await ws.send("hello")

    def process_request(connection, request):
        seen.append(request.headers.get("Authorization"))
        return None

    return await serve(handler, "127.0.0.1", 0, ssl=ctx, process_request=process_request)


async def _try(server, connector):
    port = server.sockets[0].getsockname()[1]
    kwargs = proxy._upstream_connect_kwargs(connector)
    try:
        async with connect(f"wss://127.0.0.1:{port}/", additional_headers={"Authorization": "PVEAPIToken=SECRET"},
                           open_timeout=5, **kwargs) as ws:
            return await ws.recv()
    except Exception as exc:  # noqa: BLE001
        return exc


@pytest.mark.asyncio
async def test_matching_fingerprint_connects_and_sends_the_token(tmp_path):
    crt, key, fp = _make_cert(tmp_path, "good")
    seen: list = []
    server = await _server(crt, key, seen)
    try:
        assert await _try(server, _Connector(False, fp)) == "hello"
        # colon-grouped uppercase pins identically
        grouped = ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)).upper()
        assert await _try(server, _Connector(False, grouped)) == "hello"
    finally:
        server.close()
        await server.wait_closed()
    assert seen == ["PVEAPIToken=SECRET", "PVEAPIToken=SECRET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["0" * 64, "ab" * 32, "deadbeef", ""])
async def test_wrong_fingerprint_is_refused_before_the_token_is_sent(tmp_path, wrong):
    crt, key, fp = _make_cert(tmp_path, "mitm")
    assert wrong != fp
    seen: list = []
    server = await _server(crt, key, seen)
    try:
        if wrong == "":
            # no fingerprint stored: unchanged legacy posture (unpinned) — documented, not a pin
            assert await _try(server, _Connector(False, "")) == "hello"
            return
        result = await asyncio.wait_for(_try(server, _Connector(False, wrong)), 8)
    finally:
        server.close()
        await server.wait_closed()
    assert isinstance(result, Exception), "a mismatching certificate must fail the connect"
    assert seen == [], "the PVE API token must never reach a server whose certificate does not match the pin"


@pytest.mark.asyncio
async def test_a_different_certificate_than_the_pinned_one_is_refused(tmp_path):
    good_crt, _, good_fp = _make_cert(tmp_path, "real-pve")
    evil_crt, evil_key, _ = _make_cert(tmp_path, "attacker")
    seen: list = []
    server = await _server(evil_crt, evil_key, seen)
    try:
        result = await asyncio.wait_for(_try(server, _Connector(False, good_fp)), 8)
    finally:
        server.close()
        await server.wait_closed()
    assert isinstance(result, Exception)
    assert seen == []


def test_posture_per_connector():
    assert "create_connection" not in proxy._upstream_connect_kwargs(_Connector(True, "ab" * 32))
    assert "create_connection" not in proxy._upstream_connect_kwargs(_Connector(False, None))
    assert "create_connection" in proxy._upstream_connect_kwargs(_Connector(False, "ab" * 32))
    ctx = proxy._upstream_connect_kwargs(_Connector(True, None))["ssl"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
