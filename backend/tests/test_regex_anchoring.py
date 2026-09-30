"""`re.match` with a trailing `$` accepts "value\\n": a newline smuggled into an identifier that is
later interpolated into a shell string, YAML or a PVE parameter (P5-07/P5-08). Every validator below
must reject it."""

from __future__ import annotations

import pytest


def test_trailing_newline_is_rejected_by_every_identifier_regex():
    from app.auth.dependencies import _PAT_BEARER_RE
    from app.clusters.schemas import _TOKEN_USER_RE
    from app.inventory.schemas import PVE_TAG_RE
    from app.lifecycle.resize import _DISK_KEY_RE
    from app.provisioning.cloudinit import _LINUX_USERNAME_RE

    samples = {
        "token_user": (_TOKEN_USER_RE, "gui@pve"),
        "pve_tag": (PVE_TAG_RE, "web"),
        "disk_key": (_DISK_KEY_RE, "scsi0"),
        "linux_user": (_LINUX_USERNAME_RE, "ubuntu"),
    }
    for name, (rx, ok) in samples.items():
        assert rx.fullmatch(ok), name
        for bad in (ok + "\n", ok + "\r\n", "\n" + ok, ok + "\n#extra"):
            assert not rx.fullmatch(bad), f"{name} accepts {bad!r}"
    assert _PAT_BEARER_RE is not None


@pytest.mark.parametrize("bad", ["ubuntu\n", "ubuntu\nadmin", "root\n"])
def test_cloudinit_username_cannot_inject_yaml_lines(bad):
    from app.provisioning import cloudinit

    assert not cloudinit._LINUX_USERNAME_RE.fullmatch(bad)
