"""Redis/arq hardening (F-09): unix socket support + JSON (never pickle) serialization."""

from __future__ import annotations

import pickle

import pytest

from app.config import settings
from app.jobs import redis_conf


def test_socket_configured_uses_unix_socket_and_no_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_socket", "/run/redis/redis-server.sock")
    rs = redis_conf.arq_redis_settings()
    assert rs.unix_socket_path == "/run/redis/redis-server.sock"
    assert rs.database == 0


def test_default_is_loopback_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "redis_socket", None)
    rs = redis_conf.arq_redis_settings()
    assert rs.host == "127.0.0.1" and rs.port == 6379 and rs.unix_socket_path is None


def test_serializer_round_trip_is_json() -> None:
    job = {"t": 1, "f": "vm.start", "a": [42, "x"], "k": {"_job_id": "job-42"}, "et": 1700000000000}
    blob = redis_conf.job_serializer(job)
    assert isinstance(blob, bytes)
    assert redis_conf.job_deserializer(blob) == job
    assert blob.startswith(b"{")  # JSON text, not a pickle stream


def test_deserializer_refuses_pickle_payloads() -> None:
    """A pickle blob written into Redis by another local process must never execute."""

    class Boom:
        def __reduce__(self):  # pragma: no cover - would run on unpickle
            import os
            return (os.system, ("echo pwned > /tmp/pgui-pickle-test",))

    with pytest.raises(ValueError):
        redis_conf.job_deserializer(pickle.dumps(Boom()))


def test_worker_and_pool_use_the_json_serializers() -> None:
    from app.jobs.worker import WorkerSettings

    assert WorkerSettings.job_serializer is redis_conf.job_serializer
    assert WorkerSettings.job_deserializer is redis_conf.job_deserializer
    assert WorkerSettings.redis_settings.host == redis_conf.arq_redis_settings().host


def test_main_creates_the_pool_with_the_json_serializers() -> None:
    import inspect

    import app.main as main

    src = inspect.getsource(main)
    assert "job_serializer=redis_conf.job_serializer" in src
    assert "job_deserializer=redis_conf.job_deserializer" in src
    assert "arq_redis_settings()" in src


@pytest.mark.asyncio
async def test_real_redis_unix_socket_round_trip_with_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: redis-server on a unix socket only (port 0), arq pool + worker with the JSON
    serializers run a job. Skipped when redis-server is not installed."""
    import asyncio
    import shutil
    import subprocess

    from arq import create_pool, func
    from arq.worker import Worker

    exe = shutil.which("redis-server")
    if exe is None:
        pytest.skip("redis-server not installed")
    sock = tmp_path / "r.sock"
    proc = subprocess.Popen(  # noqa: ASYNC220 - short-lived test fixture process
        [exe, "--port", "0", "--unixsocket", str(sock), "--unixsocketperm", "660",
         "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if sock.exists():
                break
            await asyncio.sleep(0.1)
        assert sock.exists(), "redis did not create its unix socket"
        monkeypatch.setattr(settings, "redis_socket", str(sock))

        seen: list[int] = []

        async def job(ctx, job_id: int) -> str:
            seen.append(job_id)
            return "done"

        rs = redis_conf.arq_redis_settings()
        pool = await create_pool(rs, job_serializer=redis_conf.job_serializer,
                                 job_deserializer=redis_conf.job_deserializer)
        enq = await pool.enqueue_job("job", 7, _job_id="job-7")
        assert enq is not None
        raw = await pool.get(b"arq:job:job-7")
        assert raw is not None and raw.startswith(b"{"), "job stored as JSON, not pickle"
        worker = Worker(functions=[func(job, name="job")], redis_settings=rs, burst=True, poll_delay=0.1,
                        job_serializer=redis_conf.job_serializer,
                        job_deserializer=redis_conf.job_deserializer)
        await worker.async_run()
        await worker.close()
        assert seen == [7]
        assert await enq.result(timeout=5) == "done"
        await pool.aclose()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_rate_limiter_uses_the_unix_socket_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limiter used to hard-code TCP 6379: with Redis on a socket only it silently fell back to
    a process-local bucket."""
    import redis

    from app.security import rate_limit

    seen = {}

    class FakeRedis:
        def __init__(self, **kw):
            seen.update(kw)

        def ping(self):
            return True

    monkeypatch.setattr(redis, "Redis", FakeRedis)
    monkeypatch.setattr(settings, "redis_socket", "/run/redis/redis-server.sock")
    monkeypatch.setattr(rate_limit, "_client", None)
    assert rate_limit._get_client() is not None
    assert seen["unix_socket_path"] == "/run/redis/redis-server.sock"
    assert "host" not in seen and "port" not in seen
    monkeypatch.setattr(rate_limit, "_client", None)
