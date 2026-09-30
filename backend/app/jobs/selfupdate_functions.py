"""arq job function for self-update (DEPLOY-04, docs/hardening/UPDATER.md).

**Runs in the WORKER process, unprivileged.** It does not download, unpack, verify, run or
restart anything (F-03): it validates the requested tag, drops a tiny request file for the
root-owned updater (started by a systemd ``.path`` unit), and mirrors the updater's
``status.json`` into the job row until a terminal state.

Sequence:
  1. Resolve the tag (admin-provided, or the latest release of the fork in ``release.conf``).
  2. Refuse if the updater is not installed or another request is pending.
  3. Write ``/var/lib/proxmox-gui/update/request`` (the tag, nothing else).
  4. Poll ``/run/proxmox-gui-updater/status.json``; statuses older than the request are ignored.
  5. ``succeeded``/``noop`` -> job succeeded; ``failed`` -> job failed with the updater's message
     (the updater already rolled back); no answer within the budget -> job failed.

The updater restarts the worker LAST, a few seconds after publishing ``succeeded``, so the job
row reaches its terminal state before this process is replaced. If the worker is restarted
mid-update anyway, the boot-time orphan reaper marks the row ``needs_review``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from app.config import settings
from app.jobs.service import finish_job, get_job, update_job
from app.selfupdate import service as selfupdate_service

logger = logging.getLogger(__name__)

#: How often to poll the status file and for how long (the updater's own budget is ~20 min).
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 1800.0


async def _fail(sessionmaker, job_id: int, *, error: str, friendly: str) -> None:
    async with sessionmaker() as db:
        await finish_job(db, job_id, state="failed", error=error, friendly=friendly)
        await db.commit()


async def _succeed(sessionmaker, job_id: int, *, friendly: str) -> None:
    async with sessionmaker() as db:
        await finish_job(db, job_id, state="succeeded", friendly=friendly)
        await db.commit()


async def run_self_update(ctx: dict, job_id: int) -> None:
    """Self-update entry point: request the root updater and mirror its status."""
    sessionmaker = ctx["sessionmaker"]

    async with sessionmaker() as db:
        job = await get_job(db, job_id)
        if job is None:
            logger.warning("run_self_update: job %s not found", job_id)
            return
        if job.state in {"succeeded", "failed", "needs_review"}:
            return
        await update_job(db, job_id, state="running", started_at=datetime.now(UTC))
        await db.commit()
        payload = json.loads(job.payload) if job.payload else {}

    # ---- 1. which release? ------------------------------------------------
    try:
        requested = payload.get("target_version")
        if requested is None:
            repo_url = selfupdate_service.read_repo_url(settings.release_conf_path)
            requested = await selfupdate_service.resolve_latest_tag(repo_url)
        tag = selfupdate_service.validate_tag(requested)
    except (ValueError, RuntimeError) as exc:
        await _fail(
            sessionmaker, job_id, error=str(exc),
            friendly="Couldn't determine which release to install.",
        )
        return

    # ---- 2. preconditions --------------------------------------------------
    if not settings.updater_path.exists():
        await _fail(
            sessionmaker, job_id, error=f"updater not installed at {settings.updater_path}",
            friendly="The updater isn't installed in this container; update from the Proxmox host "
                     "with install.sh --update.",
        )
        return

    # ---- 3. ask the updater --------------------------------------------------
    request_ts = selfupdate_service.utc_iso_now()
    try:
        await asyncio.to_thread(
            selfupdate_service.write_request, settings.update_request_path, tag
        )
    except (OSError, RuntimeError, ValueError) as exc:
        await _fail(
            sessionmaker, job_id, error=f"could not write the update request: {exc}",
            friendly="Couldn't hand the update request to the updater.",
        )
        return
    logger.info("self-update: requested %s (job %s)", tag, job_id)

    # ---- 4. mirror the updater's status ------------------------------------
    waited = 0.0
    while waited <= POLL_TIMEOUT_S:
        status = await asyncio.to_thread(
            selfupdate_service.read_status, settings.update_status_path
        )
        if status is not None and status["updated_at"] >= request_ts:
            state = status["state"]
            if state in {"succeeded", "noop"}:
                await _succeed(
                    sessionmaker, job_id,
                    friendly=status["message"] or f"Updated to {tag}.",
                )
                return
            if state == "failed":
                await _fail(
                    sessionmaker, job_id, error=status["message"] or "update failed",
                    friendly=(
                        "The update failed"
                        + (" and was rolled back" if status["rolled_back"] else "")
                        + f": {status['message']}"
                    )[:500],
                )
                return
            # state == "running": keep waiting.
        await asyncio.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S

    await _fail(
        sessionmaker, job_id, error="timed out waiting for the updater",
        friendly="The updater didn't report a result in time; check "
                 "`journalctl -u proxmox-gui-updater` inside the container.",
    )
