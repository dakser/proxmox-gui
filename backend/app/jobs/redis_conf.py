"""Redis connection + arq (de)serialization settings (F-09, D7).

* Production reaches Redis over a unix socket (``PROXMOX_GUI_REDIS_SOCKET``, set by the
  systemd units) — Redis has no TCP port at all; access is by group membership.
  Without the setting (dev/test) it falls back to loopback TCP.
* arq's default job serializer is pickle: anything that can write to Redis would get
  code execution in the worker on unpickle. Jobs here only carry ints/strings/dicts, so
  both directions use JSON, and the deserializer refuses anything that is not JSON.
"""

from __future__ import annotations

import json
from typing import Any

from arq.connections import RedisSettings

from app.config import settings


def arq_redis_settings() -> RedisSettings:
    if settings.redis_socket:
        return RedisSettings(unix_socket_path=settings.redis_socket, database=0)
    return RedisSettings(host="127.0.0.1", port=6379, database=0)


def job_serializer(obj: Any) -> bytes:
    """JSON-encode a job/result dict (``default=str`` only stringifies stray datetimes)."""
    return json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8")


def job_deserializer(blob: bytes) -> Any:
    """JSON-decode; a pickle (or any non-JSON) payload raises ``ValueError``."""
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("refusing non-JSON job payload") from exc
