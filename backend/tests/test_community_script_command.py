"""Community-script command construction (P5-07, F-13/CLAUDE.md #8): a hostile slug or commit never
reaches the shell, and the fetched script URL is always pinned to a 40-hex catalog commit."""

from __future__ import annotations

import re

import pytest

from app.jobs import provisioning_functions as pf

SHA = "369f9013088f19771a1b95c40ee252fd4c16f91b"

HOSTILE_SLUGS = [
    "; id", "$(id)", "`id`", "../etc/passwd", "a/../b", "a/b", "https://evil.example/x", "http://x", "a b", "a\nb",
    "docker\n", "docker\r", "docker\n; id", "-docker", "Docker", "DOCKER", "dock_er", "dock.er", "a;b", "a&b", "a|b",
    "a>b", "a<b", "a'b", 'a"b', "a\\b", "a\x00b", "", " ", "é", "docker ", " docker", "a%20b", "a%2e%2e", "${IFS}id",
    "a" * 0, "*", "a*", "?", "{a,b}", "~", "a#b", "a$b", "a!b", "a\tb",
]

HOSTILE_SHAS = [
    "main", "master", "HEAD", "v1.0.0", "", SHA[:39], SHA + "0", SHA.upper(), SHA + "\n", "\n" + SHA, SHA[:-1] + "g",
    "$(id)" + SHA[5:], SHA[:20] + "; id" + SHA[24:], SHA[:39] + " ", "../" + SHA, "0" * 40 + "\n",
]


@pytest.mark.parametrize("slug", HOSTILE_SLUGS)
def test_hostile_slugs_never_produce_a_command(slug):
    with pytest.raises(ValueError):
        pf._validate_slug(slug)
    with pytest.raises(ValueError):
        pf._build_install_command(slug=slug, commit_sha=SHA)


@pytest.mark.parametrize("sha", HOSTILE_SHAS)
def test_only_a_full_lowercase_sha1_is_a_valid_pin(sha):
    with pytest.raises(ValueError):
        pf._validate_commit_sha(sha)
    with pytest.raises(ValueError):
        pf._build_install_command(slug="docker", commit_sha=sha)


@pytest.mark.parametrize("slug", ["docker", "home-assistant", "pihole", "n8n", "a", "0ad", "vaultwarden-2"])
def test_valid_slugs_build_a_pinned_command(slug):
    argv = pf._build_install_command(slug=slug, commit_sha=SHA)
    assert argv[:2] == ["bash", "-c"] and len(argv) == 3
    url = f"https://raw.githubusercontent.com/community-scripts/ProxmoxVE/{SHA}/install/{slug}-install.sh"
    assert argv[2] == f'yes y | bash -c "$(curl -fsSL {url})"'
    # the only URL in the command is the commit-anchored one
    assert re.findall(r"https?://\S+?(?=\)|\s|$)", argv[2]) == [url]
    assert "/main/" not in argv[2] and "/master/" not in argv[2]


def test_the_command_contains_no_operator_supplied_text_beyond_slug_and_sha():
    argv = pf._build_install_command(slug="docker", commit_sha=SHA)
    stripped = argv[2].replace(SHA, "SHA").replace("docker", "SLUG")
    assert stripped == (
        'yes y | bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/SHA/install/SLUG-install.sh)"'
    )


@pytest.mark.parametrize("bad", ["a b", "a;b", "$(id)", "a\nb", "-x", "1a b", "A"])
def test_env_names_are_fixed_and_values_never_become_command_text(bad):
    """Operator-controlled values travel only as env VALUES in the gate JSON (never in argv)."""
    env = pf._build_install_env({"vmid": 201, "script_slug": "docker", "application": bad, "config": {}})
    assert set(env) >= {"CTID", "app", "APPLICATION", "PASSWORD"}
    assert env["APPLICATION"] == bad
    argv = pf._build_install_command(slug="docker", commit_sha=SHA)
    assert bad not in argv[2] or bad in {"A"}


@pytest.mark.asyncio
async def test_a_hostile_slug_is_rejected_before_any_lxc_is_created(session_factory, monkeypatch):
    """run_community_script re-validates at the job boundary (WR-01): fail before stage 1."""
    import json

    from app.models import Job

    created = []

    class Reg:
        async def get_for_team(self, **kw):
            class C:
                async def create_lxc(self, **k):
                    created.append(k)
                    return "UPID"
            return C()

    async with session_factory() as db:
        job = Job(kind="lxc.community-script", cluster_id=None, team_id=None, actor_user_id=None, state="pending",
                  payload=json.dumps({"node": "pve", "vmid": 201, "config": {"ostemplate": "x"},
                                      "script_slug": "docker; id", "commit_sha": SHA}))
        db.add(job)
        await db.commit()
        await db.refresh(job)
        jid = job.id

    async def fake_claim(ctx, job_id, fn_name):
        async with session_factory() as db:
            return await db.get(Job, job_id)

    monkeypatch.setattr("app.jobs.clone_migrate_functions._claim", fake_claim)

    class R:
        async def publish(self, *a, **k): ...
        async def aclose(self): ...

    await pf.run_community_script({"sessionmaker": session_factory, "registry": Reg(), "redis": R()}, jid)
    assert created == []
    async with session_factory() as db:
        assert (await db.get(Job, jid)).state == "failed"
