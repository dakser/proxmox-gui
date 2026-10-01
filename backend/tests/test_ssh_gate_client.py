"""Backend side of the SSH gate protocol (docs/hardening/SSH-GATE.md, F-01/F-11)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.clusters import ssh_gate
from app.config import Settings


def _settings(**kw) -> Settings:
    base = dict(
        jwt_secret="x", pat_pepper="y",
        ssh_key_path=Path("/etc/proxmox-gui/gui_ed25519"),
        ssh_known_hosts_path=Path("/var/lib/proxmox-gui/ssh/known_hosts"),
        community_scripts_enabled=True,
    )
    base.update(kw)
    return Settings(**base)


def test_ssh_argv_is_exact_and_hardened() -> None:
    argv = ssh_gate.build_ssh_argv(_settings(), "pve-1", "exec 201", connect_timeout=12)
    assert argv == [
        "ssh",
        "-i", "/etc/proxmox-gui/gui_ed25519",
        "-o", "IdentitiesOnly=yes",
        "-o", "UserKnownHostsFile=/var/lib/proxmox-gui/ssh/known_hosts",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes",
        "-o", "ClearAllForwardings=yes",
        "-o", "ConnectTimeout=12",
        "-T",
        "root@pve-1",
        "exec 201",
    ]


@pytest.mark.parametrize(
    "node",
    ["pve 1", "-oProxyCommand=id", "a;b", "$(id)", "", "-", "a" * 64, "pve-1\nx", "a b", "root@evil", "..", "a..b/../c"],
)
def test_hostile_node_names_rejected_before_any_process(node: str) -> None:
    with patch("subprocess.Popen") as popen, patch("asyncio.create_subprocess_exec") as cse:
        with pytest.raises(ValueError):
            ssh_gate.build_ssh_argv(_settings(), node, "exec 201")
        popen.assert_not_called()
        cse.assert_not_called()


@pytest.mark.parametrize("node", ["pve", "pve-01", "node1.example.org", "A1", "10.0.0.5"])
def test_valid_node_names_accepted(node: str) -> None:
    assert ssh_gate.build_ssh_argv(_settings(), node, "preflight")[-2] == f"root@{node}"


def test_exec_request_is_one_json_line_then_stdin() -> None:
    payload = ssh_gate.build_exec_payload(
        command=["bash", "-c", "echo $(id); `x`"], env={"CTID": "201", "app": "a b"}, stdin_data="y\ny\n",
    )
    first, _, rest = payload.partition("\n")
    assert json.loads(first) == {"env": {"CTID": "201", "app": "a b"}, "argv": ["bash", "-c", "echo $(id); `x`"]}
    assert rest == "y\ny\n"
    assert "\n" not in first


def test_exec_request_without_stdin() -> None:
    assert ssh_gate.build_exec_payload(command=["true"], env=None, stdin_data=None).endswith("\n")


@pytest.mark.parametrize("vmid", [0, 99, -5, 10**9])
def test_exec_command_rejects_bad_vmid(vmid: int) -> None:
    with pytest.raises(ValueError):
        ssh_gate.exec_remote_command(vmid)


def test_exec_command_format() -> None:
    assert ssh_gate.exec_remote_command(201) == "exec 201"


def test_ssh_pct_exec_uses_gate_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ssh_pct_exec must not build a remote shell string any more."""
    from app.clusters import connector as conn_mod

    monkeypatch.setattr(conn_mod, "gate_settings", lambda: _settings())
    captured: dict = {}

    class FakeProc:
        def __init__(self, argv, **kw):
            captured["argv"] = argv
            self.stdin = MagicMock()
            self.stdin.write.side_effect = lambda d: captured.setdefault("stdin", d)
            self.stdout = iter(["ok\n"])
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("subprocess.Popen", FakeProc)
    c = conn_mod.PVEConnector.__new__(conn_mod.PVEConnector)
    result = c._ssh_pct_exec(
        node="pve-1", vmid=201, command=["bash", "-c", "x; id"], stdin_data="y\n",
        env={"CTID": "201"}, on_output=None, timeout=60.0,
    )
    assert result == {"exit_code": 0, "output": "ok\n"}
    assert captured["argv"][-1] == "exec 201"
    assert "root@pve-1" in captured["argv"]
    assert "StrictHostKeyChecking=yes" in captured["argv"]
    assert "accept-new" not in " ".join(captured["argv"])
    assert json.loads(captured["stdin"].split("\n", 1)[0])["argv"] == ["bash", "-c", "x; id"]


def test_ssh_pct_exec_refuses_hostile_node_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.clusters import connector as conn_mod

    monkeypatch.setattr(conn_mod, "gate_settings", lambda: _settings())
    popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen)
    c = conn_mod.PVEConnector.__new__(conn_mod.PVEConnector)
    with pytest.raises(ValueError):
        c._ssh_pct_exec(node="x -oProxyCommand=id", vmid=201, command=["true"], stdin_data=None,
                        env=None, on_output=None, timeout=10.0)
    popen.assert_not_called()


def test_ssh_pct_exec_refuses_when_channel_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.clusters import connector as conn_mod
    from app.clusters.errors import PVEUnreachable

    monkeypatch.setattr(conn_mod, "gate_settings", lambda: _settings(community_scripts_enabled=False))
    popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", popen)
    c = conn_mod.PVEConnector.__new__(conn_mod.PVEConnector)
    with pytest.raises(PVEUnreachable):
        c._ssh_pct_exec(node="pve-1", vmid=201, command=["true"], stdin_data=None,
                        env=None, on_output=None, timeout=10.0)
    popen.assert_not_called()
