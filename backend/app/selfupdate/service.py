"""Building blocks for the self-update handshake (DEPLOY-04, docs/hardening/UPDATER.md).

The worker never downloads, extracts, verifies or executes a release: a root-owned updater
(``/usr/local/sbin/proxmox-gui-updater``, started by a systemd ``.path`` unit) does all of
that and only accepts releases signed by the fork owner's key. What lives here is only:

- :func:`validate_tag` — the strict release-tag grammar shared with the updater.
- :func:`read_repo_url` / :func:`resolve_latest_tag` — pick a tag when the admin did not
  name one (purely a convenience: the updater re-validates and verifies the signature, so
  a lying GitHub API can at worst make the update fail).
- :func:`write_request` / :func:`read_status` — the file protocol with the updater.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

#: Same grammar as the updater's TAG_RE: v<MAJOR>.<MINOR>.<PATCH>[-prerelease].
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?$")
_REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})$")
MAX_TAG_LEN = 64

_STATE_VALUES = {"running", "succeeded", "failed", "noop"}


def validate_tag(value: str | None) -> str:
    """Return ``value`` if it is a clean release tag, else raise ``ValueError``."""
    if (
        not isinstance(value, str)
        or len(value) > MAX_TAG_LEN
        or ".." in value
        or not TAG_RE.fullmatch(value)
    ):
        raise ValueError(f"invalid release tag: {str(value)[:40]!r}")
    return value


def read_repo_url(release_conf: Path) -> str:
    """The fork this LXC updates from, as pinned by the installer in ``release.conf``."""
    try:
        text = release_conf.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read {release_conf}: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("REPO_URL="):
            url = line[len("REPO_URL="):].strip()
            if _REPO_RE.fullmatch(url) and ".." not in url and not url.endswith(".git"):
                return url
    raise RuntimeError(f"{release_conf} has no valid REPO_URL")


async def resolve_latest_tag(repo_url: str) -> str:
    """Ask GitHub for the latest release tag of ``repo_url`` (informational only)."""
    match = _REPO_RE.fullmatch(repo_url)
    if match is None:
        raise RuntimeError("invalid repository URL")
    owner, repo = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        resp = await client.get(api, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code != 200:
        raise RuntimeError(f"could not determine the latest release (HTTP {resp.status_code})")
    return validate_tag(resp.json().get("tag_name"))


def write_request(path: Path, tag: str) -> None:
    """Atomically write the update request (a bare tag). Fails if one is already pending."""
    validate_tag(tag)
    if path.exists() or path.is_symlink():
        raise RuntimeError("an update request is already pending")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".request-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(tag)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_status(path: Path) -> dict[str, Any] | None:
    """Parse the updater's status file; ``None`` when absent or not a well-formed object."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if not isinstance(state, str) or state not in _STATE_VALUES:
        return None
    return {
        "state": state,
        "step": str(data.get("step", ""))[:80],
        "target": str(data.get("target", ""))[:80],
        "message": str(data.get("message", ""))[:300],
        "rolled_back": bool(data.get("rolled_back", False)),
        "updated_at": str(data.get("updated_at", ""))[:40],
    }


def utc_iso_now() -> str:
    """Second-resolution UTC timestamp in the updater's format (``%Y-%m-%dT%H:%M:%SZ``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
