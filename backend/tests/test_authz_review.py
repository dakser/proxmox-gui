"""P5-08 targeted authorization review — every case here failed against the pre-review code."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from tests.factories import login_as, make_user

PW = "testpass12345"


async def _job(session_factory, *, team_id, kind="vm.power", state="failed", payload="{}"):
    from app.models import Job

    async with session_factory() as s:
        j = Job(kind=kind, state=state, team_id=team_id, payload=payload)
        s.add(j)
        await s.commit()
        return j.id


async def _drop_memberships(session_factory, user_id):
    from app.models import TeamMembership

    async with session_factory() as s:
        await s.execute(delete(TeamMembership).where(TeamMembership.user_id == user_id))
        await s.commit()


@pytest.mark.asyncio
async def test_user_without_any_team_sees_no_jobs(client, session_factory):
    """`if team_ids:` used to skip the filter for an empty team set → the caller saw EVERY tenant's jobs."""
    u = await make_user(session_factory, username="loner", is_admin=False)
    await _drop_memberships(session_factory, u.id)
    other = await _job(session_factory, team_id=None)
    cookies = await login_as(client, username="loner", password=PW)
    body = (await client.get("/api/v1/jobs", cookies=cookies)).json()
    assert body["jobs"] == []
    assert other not in {j["id"] for j in body["jobs"]}


@pytest.mark.asyncio
async def test_user_without_any_team_gets_an_empty_ws_backfill_set(session_factory):
    from app.jobs import service

    await _job(session_factory, team_id=None)
    async with session_factory() as db:
        assert await service.list_recent_jobs(db, []) == []
        assert await service.list_jobs(db, [], state="failed") == []


@pytest.mark.asyncio
async def test_system_jobs_are_admin_only(client, session_factory):
    """team_id=None jobs (self-update, boot jobs) leaked to every authenticated user via GET/retry."""
    await make_user(session_factory, username="plainuser", is_admin=False)
    await make_user(session_factory, username="rootadm", is_admin=True)
    jid = await _job(session_factory, team_id=None, kind="admin.self-update",
                     payload='{"target_version":"v9.9.9","triggered_by_user_id":1}')
    cookies = await login_as(client, username="plainuser", password=PW)
    assert (await client.get(f"/api/v1/jobs/{jid}", cookies=cookies)).status_code == 404
    r = await client.post(f"/api/v1/jobs/{jid}/retry", cookies=cookies, headers={"X-CSRF-Token": cookies["csrf_token"]})
    assert r.status_code == 404
    admin = await login_as(client, username="rootadm", password=PW)
    assert (await client.get(f"/api/v1/jobs/{jid}", cookies=admin)).status_code == 200


@pytest.mark.asyncio
async def test_job_payload_is_not_exposed(client, session_factory):
    await make_user(session_factory, username="rootadm2", is_admin=True)
    jid = await _job(session_factory, team_id=None, payload='{"secret":"do-not-leak"}')
    cookies = await login_as(client, username="rootadm2", password=PW)
    assert "do-not-leak" not in (await client.get(f"/api/v1/jobs/{jid}", cookies=cookies)).text


@pytest.mark.asyncio
async def test_system_job_events_reach_admin_sockets_only():
    """A team-less event used to be broadcast to EVERY connected socket."""
    from app.jobs.events import ConnectionManager

    class WS:
        def __init__(self):
            self.got = []

        async def send_json(self, ev):
            self.got.append(ev)

    mgr = ConnectionManager()
    admin, member, loner = WS(), WS(), WS()
    mgr.add(admin, [1], is_admin=True)
    mgr.add(member, [1])
    mgr.add(loner, [])
    await mgr.broadcast({"type": "job.completed", "job": {"id": 1, "team_id": None, "kind": "admin.self-update"}})
    assert len(admin.got) == 1 and member.got == [] and loner.got == []
    await mgr.broadcast({"type": "job.updated", "job": {"id": 2, "team_id": 1}})
    assert len(admin.got) == 2 and len(member.got) == 1 and loner.got == []
    mgr.remove(admin)
    await mgr.broadcast({"type": "job.completed", "job": {"id": 3, "team_id": None}})
    assert len(admin.got) == 2


# ---------------------------------------------------------------------------
# Route inventory: nothing new can ship unauthenticated or without CSRF by accident.
# ---------------------------------------------------------------------------

_PUBLIC = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),      # authenticates with the refresh cookie itself
    ("POST", "/api/v1/auth/keepalive"),    # idem
    ("POST", "/api/v1/auth/logout"),       # idem
    ("GET", "/api/v1/setup/status"),
    ("POST", "/api/v1/setup/admin"),       # setup-token gated (F-06)
}
_NO_CSRF_OK = {p for _m, p in _PUBLIC if p.startswith(("/api/v1/auth", "/api/v1/setup"))}


def _deps(dep):
    out = set()
    for d in dep.dependencies:
        out.add(getattr(d.call, "__name__", str(d.call)))
        out |= _deps(d)
    return out


def test_every_route_requires_authentication_except_the_public_allow_list():
    from fastapi.routing import APIRoute

    from app.main import create_app

    unauth = set()
    for r in create_app().routes:
        if isinstance(r, APIRoute) and not (_deps(r.dependant) & {"get_current_principal", "require_admin"}):
            unauth |= {(m, r.path) for m in r.methods}
    assert unauth == _PUBLIC, f"unexpected unauthenticated routes: {sorted(unauth ^ _PUBLIC)}"


def test_every_mutating_route_has_csrf_protection_except_login_flow_and_setup():
    from fastapi.routing import APIRoute

    from app.main import create_app

    missing = []
    for r in create_app().routes:
        if isinstance(r, APIRoute) and (r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            if "csrf_protect" not in _deps(r.dependant) and r.path not in _NO_CSRF_OK:
                missing.append(r.path)
    assert missing == [], f"mutating routes without csrf_protect: {missing}"
