"""`re.match` with a trailing `$` accepts "value\\n": a newline smuggled into an identifier that is later
interpolated into a shell string, YAML or a PVE parameter (P5-07/P5-08, F-18). These tests go through the
real call sites (schemas / validators), so they fail if any of them goes back to `.match`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize("bad", ["gui@pve\n", "gui@pve\r\n", "\ngui@pve", "gui@pve\n#x"])
def test_cluster_token_user_rejects_trailing_newline(bad):
    from app.clusters.schemas import ClusterCreate, ClusterUpdate

    common = dict(host="pve.example.test", token_name="t", api_token_secret="s")
    with pytest.raises(ValidationError):
        ClusterCreate(name="c", token_user=bad, **common)
    with pytest.raises(ValidationError):
        ClusterUpdate(token_user=bad, api_token_secret="s")
    ClusterCreate(name="c", token_user="gui@pve", **common)  # control: the clean value passes


@pytest.mark.parametrize("bad", ["web\n", "web\r\n", "\nweb", "web\n#x"])
def test_inventory_tags_reject_trailing_newline(bad):
    from app.inventory.schemas import TagsUpdate

    with pytest.raises(ValidationError):
        TagsUpdate(tags=[bad])
    assert TagsUpdate(tags=["web"]).tags == ["web"]


@pytest.mark.parametrize("bad", ["ubuntu\n", "ubuntu\nadmin", "root\n"])
def test_cloudinit_username_cannot_inject_yaml_lines(bad):
    from app.provisioning.cloudinit import CloudInitForm, validate_cloudinit_form

    verdict = validate_cloudinit_form(CloudInitForm(ciuser=bad, cipassword="a-long-enough-pass"))
    assert any(e.field == "ciuser" for e in verdict.hard_errors), verdict
    ok = validate_cloudinit_form(CloudInitForm(ciuser="ubuntu", cipassword="a-long-enough-pass"))
    assert not any(e.field == "ciuser" for e in ok.hard_errors)


@pytest.mark.parametrize("bad", ["scsi0\n", "rootfs\n", "mp0\n", "\nscsi0", "virtio1\r\n"])
def test_disk_keys_reject_trailing_newline(bad):
    from app.lifecycle.resize import parse_disk_sizes

    assert parse_disk_sizes({bad: "local-lvm:vm-1-disk-0,size=32G"}) == {}
    assert parse_disk_sizes({"scsi0": "local-lvm:vm-1-disk-0,size=32G"}) == {"scsi0": 32}


@pytest.mark.parametrize("bad", ["pat_abcdefgh\n", "pat_abcdefgh\r\n"])
def test_pat_bearer_pattern_rejects_trailing_newline(bad):
    from app.auth.dependencies import _PAT_BEARER_RE

    assert not _PAT_BEARER_RE.fullmatch(bad)
    assert _PAT_BEARER_RE.fullmatch("pat_abcdefgh")
