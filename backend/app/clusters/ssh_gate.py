"""Client side of the SSH gate protocol (docs/hardening/SSH-GATE.md).

The GUI LXC talks to the PVE node through a forced-command SSH key: the node's
``authorized_keys`` entry pins ``command="/usr/local/sbin/proxmox-gui-ssh-gate"``, so
the only things this client can ask for are ``preflight`` and ``exec <vmid>`` —
the latter with a JSON request line on stdin. No remote shell string is ever
built here (F-01), the node name is validated before a process is spawned (F-11)
and host keys are pinned (StrictHostKeyChecking=yes against a known_hosts file the
installer populated).
"""

from __future__ import annotations

import json
import re

from app.config import Settings, settings

_NODE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$")
_VMID_RE = re.compile(r"^[1-9][0-9]{2,8}$")

CHANNEL_DISABLED_DETAIL = (
    "The community-scripts SSH channel is disabled. Re-run the installer with "
    "--enable-community-scripts to enable it (see deploy/README.md)."
)


def gate_settings() -> Settings:
    """Indirection so tests can swap the settings the SSH client reads."""
    return settings


def validate_node(node: str) -> str:
    if not isinstance(node, str) or ".." in node or not _NODE_RE.fullmatch(node):
        raise ValueError(f"invalid node name: {node!r}")
    return node


def exec_remote_command(vmid: int) -> str:
    if not _VMID_RE.fullmatch(str(vmid)):
        raise ValueError(f"invalid vmid: {vmid!r}")
    return f"exec {int(vmid)}"


def build_ssh_argv(
    cfg: Settings, node: str, remote_command: str, *, connect_timeout: int = 10
) -> list[str]:
    """Exact ``ssh`` argv for one gate call. ``remote_command`` is a gate verb."""
    validate_node(node)
    return [
        "ssh",
        "-i", str(cfg.ssh_key_path),
        "-o", "IdentitiesOnly=yes",
        "-o", f"UserKnownHostsFile={cfg.ssh_known_hosts_path}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes",
        "-o", "ClearAllForwardings=yes",
        "-o", f"ConnectTimeout={int(connect_timeout)}",
        "-T",
        f"root@{node}",
        remote_command,
    ]


def build_exec_payload(
    *, command: list[str], env: dict[str, str] | None, stdin_data: str | None
) -> str:
    """JSON request line + the process stdin, as the gate expects on stdin."""
    request = {
        "env": {str(k): str(v) for k, v in (env or {}).items()},
        "argv": [str(a) for a in command],
    }
    return json.dumps(request, separators=(",", ":")) + "\n" + (stdin_data or "")
