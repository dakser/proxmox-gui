"""``/api/v1/setup`` HTTP routes — first-run wizard backend.

NO authentication on either endpoint:

- ``GET /status`` is read-only and never reveals secrets — the predicate
  flags are usable by an unauthenticated frontend to render the wizard.
- ``POST /admin`` is open IFF :func:`app.setup.service.no_admin_yet`
  returns True. Once an admin exists the endpoint returns 409.

NO CSRF dependency on either endpoint: there is no session yet, so there
is no ``csrf_token`` cookie to compare against. The double-submit pattern
applies only to authenticated cookie-session routes.

There is intentionally NO ``/api/v1/setup/cluster`` route. Cluster
registration during the wizard goes through the authenticated admin's
session via :mod:`app.clusters.routes` (Plan 08's UI auto-logs-in after
the admin step and presents the cluster step as an authenticated screen).
This is per CONTEXT D-18 (lenient first-run): the only mandatory step is
admin creation.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.core.source_ip import extract_source_ip
from app.security.rate_limit import check_rate
from app.setup import service
from app.setup.schemas import (
    SetupAdminRequest,
    SetupAdminResponse,
    SetupStatusResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

#: Failed/any attempts per source IP on the token-gated endpoint (brute-force guard).
SETUP_RATE_LIMIT = 5
SETUP_RATE_WINDOW_S = 60.0
_MAX_TOKEN_LEN = 256


def _load_setup_token() -> str | None:
    """``None`` when no token is configured (dev); raises 503 if configured but unreadable."""
    path = settings.setup_token_file
    if path is None:
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if not value:
        # Fail closed: a configured-but-missing token must never open the endpoint.
        logger.error("setup token file %s is missing or empty", path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Setup is not available; the setup token is not provisioned.",
        )
    return value


@router.get(
    "/status",
    response_model=SetupStatusResponse,
    summary="First-run setup predicate flags (open endpoint)",
    operation_id="setup_status",
)
async def setup_status(
    db: AsyncSession = Depends(get_db),
) -> SetupStatusResponse:
    """Returns ``{no_admin_yet, cluster_count}``.

    Always 200. Open endpoint: the SPA needs to know whether to render
    the wizard before any user has logged in.
    """
    return SetupStatusResponse(
        no_admin_yet=await service.no_admin_yet(db),
        cluster_count=await service.cluster_count(db),
        token_required=settings.setup_token_file is not None,
    )


@router.post(
    "/admin",
    response_model=SetupAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the initial admin user (one-shot, gated on no_admin_yet)",
    operation_id="setup_create_admin",
)
async def setup_create_admin(
    payload: SetupAdminRequest,
    request: Request,
    x_setup_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> SetupAdminResponse:
    """Create the very first admin + their personal team.

    Returns 409 if an admin already exists (one-shot endpoint). Returns
    422 on schema validation failure (password < 12, bad username, bad
    email).

    The frontend (Plan 08 wizard step 2) auto-logs-in via
    ``POST /api/v1/auth/login`` immediately after this 201.
    """
    expected = _load_setup_token()
    if expected is not None:
        ip = extract_source_ip(request) or "unknown"
        if not check_rate(f"setup:{ip}", limit=SETUP_RATE_LIMIT, window=SETUP_RATE_WINDOW_S):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts; please wait and retry",
            )
        supplied = (x_setup_token or "")[:_MAX_TOKEN_LEN]
        # Constant-time comparison; one generic error for missing AND wrong tokens, and the
        # same answer whether or not an admin already exists (no state oracle without the token).
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid setup token"
            )
    user, team = await service.create_initial_admin(
        db,
        username=payload.username,
        email=payload.email,
        password=payload.password,
    )
    return SetupAdminResponse(
        user_id=user.id,
        personal_team_id=team.id,
        username=user.username,
    )
