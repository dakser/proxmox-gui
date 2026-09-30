# deploy/ — installing, updating and removing the Proxmox GUI

The GUI runs in **one unprivileged LXC** on a Proxmox VE 8.x node. `install.sh` runs **on the PVE host as root**; everything
it installs comes from a **release that you (the fork owner) signed**. Nothing is installed from a branch, from `main`,
or from an unsigned download. Design notes and findings: [`docs/hardening/`](../docs/hardening/).

## Prerequisites

- Proxmox VE 8.x host with `pct`, `pvesh`, `curl`, `tar`, `sha256sum`, `ssh-keygen` (all present on a stock PVE node).
- A **published, signed release** of your fork (see [Publishing a release](#publishing-a-release-fork-owner)).
- Outbound HTTPS from the host and the LXC to `github.com` (release assets), `deb.debian.org`, `pypi.org`, `nodejs.org`.
- A free LXC id (or pass `--ctid`), 2 vCPU / 2 GB RAM / 8 GB disk by default.
- **First try it on a lab node or a nested PVE, with a snapshot** — this installer has been tested against simulated
  host tools and a real systemd in CI, not against every PVE setup (see `docs/hardening/LAB-CHECKLIST.md`).

## Secure install flow (recommended)

Do **not** pipe the installer into a shell. Download it, verify it, read it, run it:

```bash
REPO=dakser/proxmox-gui        # your fork
TAG=v0.7.0                     # a published release
mkdir -p ~/pgui && cd ~/pgui
curl -fsSLO "https://github.com/$REPO/releases/download/$TAG/install.sh"
curl -fsSLO "https://github.com/$REPO/releases/download/$TAG/SHA256SUMS"
curl -fsSLO "https://github.com/$REPO/releases/download/$TAG/SHA256SUMS.sig"

# 1. Verify the signature with YOUR public key (kept out of band, e.g. from your password manager):
printf 'proxmox-gui-release namespaces="proxmox-gui-release" %s\n' "ssh-ed25519 AAAA...your-public-key" > allowed_signers
ssh-keygen -Y verify -f allowed_signers -I proxmox-gui-release -n proxmox-gui-release \
    -s SHA256SUMS.sig < SHA256SUMS
# 2. Check that the installer you downloaded is the one that was signed:
sha256sum --ignore-missing -c SHA256SUMS      # must print: install.sh: OK
# 3. Read it (about 500 lines), then run it:
less install.sh
bash install.sh --release "$TAG"
```

`install.sh` then **re-verifies** the release tarball on the host (signature + SHA-256, using the public key embedded in the
`install.sh` you just verified) *before* it creates or touches anything, and refuses on any mismatch. Then it:

1. creates an **unprivileged** LXC (`nesting=1,keyctl=1`), tagged `proxmox-gui` and marked in its description;
2. pushes the verified tarball in, re-checks its hash inside the LXC and runs `bootstrap.sh` from it;
3. prints the URL of the first-run wizard and the command that reads the **setup token**.

### Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--release vX.Y.Z` | *(required)* | Only release tags; branches and `latest` are refused. |
| `--ctid N` | `pvesh get /cluster/nextid` | An existing CTID is never reused for an install. |
| `--hostname X` | `proxmox-gui` | Not inherited from the node's hostname. |
| `--cpu N` / `--ram MB` / `--disk GB` | `2` / `2048` / `8` | Numeric ranges are validated. |
| `--storage S` / `--bridge B` | `local-lvm` / `vmbr0` | |
| `--ip CIDR --gw ADDR` | DHCP | Recommended together with `--enable-community-scripts`. |
| `--repo-url URL` | your fork (set by `scripts/set-fork.sh`) | Only `https://github.com/<owner>/<repo>`. |
| `--signers FILE` | key embedded in `install.sh` | Alternative `allowed_signers` file. |
| `--enable-community-scripts` | off | Opt-in SSH channel, see below. |
| `--update` / `--uninstall [--purge]` | | See below. |

Environment fallbacks use the `PGUI_` prefix (`PGUI_CTID`, `PGUI_CT_HOSTNAME`, `PGUI_CPU`, …). Generic names such as `CTID` or
`HOSTNAME` from your shell are ignored on purpose.

### First-run wizard and the setup token

Open `https://<LXC-IP>/setup` (accept the local-CA certificate). The wizard asks for a **setup token**; without it nobody who
merely reaches the port can become admin. Read it on the PVE host:

```bash
pct exec <ctid> -- cat /etc/proxmox-gui/setup-token
```

Attempts are rate limited; once the admin exists the endpoint is closed.

## Updating

```bash
bash install.sh --update --ctid <ctid> --release vX.Y.Z      # from the host, same verification as an install
```

or from the UI (**Admin → Settings → Update**). Either way the work is done by a **root-owned updater inside the LXC**
(`/usr/local/sbin/proxmox-gui-updater`), triggered by systemd; the unprivileged web/worker processes only leave a request.
It installs **only releases signed by the key stored in `/etc/proxmox-gui/release-signers`**, never downgrades (unless you pass
`--allow-downgrade` from the root console), validates the tarball before unpacking, backs up the database, migrates, switches
atomically, checks `/api/v1/health` and **rolls back automatically** on failure. Details: [`docs/hardening/UPDATER.md`](../docs/hardening/UPDATER.md).

Manual control inside the LXC: `proxmox-gui-updater status`, `proxmox-gui-updater rollback [--restore-db]`.

## Community-scripts (optional, off by default)

Community-scripts installs software **inside a container over SSH from the GUI LXC to the PVE node**. Enabling it is a
deliberate trust decision:

```bash
bash install.sh --update --ctid <ctid> --release <installed tag> --enable-community-scripts --ip ... 
```

What it does: installs a forced-command gate on the node (`/usr/local/sbin/proxmox-gui-ssh-gate`) and adds
`restrict,from="<LXC IP>",command="<gate>"` to root's `authorized_keys` for the GUI's key (no shell, no forwarding). The gate only
runs `pct exec` inside **unprivileged containers tagged `proxmox-gui`**, with the arguments passed literally.

**What it implies (residual risk):** anyone who can run code in the GUI LXC can run commands *inside those tagged containers*
(not on the host). Anyone who can put the tag `proxmox-gui` on another unprivileged CT in PVE extends that reach to it. Protocol:
[`docs/hardening/SSH-GATE.md`](../docs/hardening/SSH-GATE.md). With DHCP the `from=` pin follows the LXC's *current* address —
prefer `--ip/--gw`.

Disable it any time: `bash install.sh --uninstall --ctid <ctid>` removes the key entry (and the gate when it was the last one).

## Uninstalling

```bash
bash install.sh --uninstall --ctid <ctid>            # revoke SSH trust; the LXC is left in place
bash install.sh --uninstall --ctid <ctid> --purge    # also destroy the LXC (you must type the CTID)
```

`--purge` only ever touches a container that carries the installer's tag and marker.

## Threat model in short

| Assumption | Consequence |
|---|---|
| The **PVE host** and **your signing key** are trusted. | A compromised host or key can install anything. Keep the key offline; it never touches CI. |
| The **GUI LXC's app user** (`proxmox-gui`) can be compromised (RCE in an API dependency). | It can read the encrypted token DB and `master.key` (same host) — Proxmox tokens are per-tenant, privilege-separated pool tokens to limit impact. It **cannot** modify code or units (root-owned), reach root inside the LXC via sudo (none), or use the host SSH beyond the gate. |
| The **web process** (`proxmox-gui-web`, Node) can be compromised. | It has no access to `/etc/proxmox-gui`, `/var/lib/proxmox-gui` or Redis. |
| A **release** may be tampered with in transit or at rest. | Refused: signature + SHA-256 are verified on the host and again in the LXC; tarballs are validated before extraction; versions only move forward. |
| Someone reaches the wizard first. | The setup token is required. |

Not covered: a compromised PVE host, a stolen signing key, a malicious *signed* release, physical access, and everything on
the Proxmox side (patching PVE itself, who can create tokens/tags).

## Persistent state and secrets

| Path (inside the LXC) | What | Owner / mode |
|---|---|---|
| `/etc/proxmox-gui/master.key` | 32 random bytes, Fernet root key for the stored PVE tokens | `root:proxmox-gui` `0440` |
| `/etc/proxmox-gui/jwt.secret`, `pat.pepper` | JWT signing secret; PAT hashing pepper | `root:proxmox-gui` `0440` |
| `/etc/proxmox-gui/setup-token` | one-time first-run token | `root:proxmox-gui` `0440` |
| `/etc/proxmox-gui/release.conf`, `release-signers`, `pins.env` | where and by whom updates are trusted | `root:root` `0644` |
| `/var/lib/proxmox-gui/app.db` | SQLite database | `proxmox-gui` (dir `0750`) |
| `/var/lib/proxmox-gui-updater/backups/` | pre-update DB backups (last 5) | `root:root` `0700` |

Back up `master.key` **together with** `app.db`; one without the other is useless.

### Rotating secrets

*JWT secret / PAT pepper* — replaces the value; all sessions are logged out (JWT) or all personal access tokens stop working
(pepper; users must issue new ones):

```bash
pct exec <ctid> -- bash -c 'umask 077; head -c 36 /dev/urandom | base64 | tr -d "\n=" | tr "+/" "-_" | cut -c1-48 > /etc/proxmox-gui/jwt.secret.new
  chown root:proxmox-gui /etc/proxmox-gui/jwt.secret.new; chmod 0440 /etc/proxmox-gui/jwt.secret.new
  mv -f /etc/proxmox-gui/jwt.secret.new /etc/proxmox-gui/jwt.secret; systemctl restart proxmox-gui-api proxmox-gui-worker'
```

(same for `pat.pepper`.)

*Master key* — the stored PVE API tokens are encrypted with it, so it must be **re-encrypted, not just replaced**. Inside the LXC as root:

```bash
systemctl stop proxmox-gui-api proxmox-gui-worker
cp -a /var/lib/proxmox-gui/app.db /root/app.db.before-rotation          # keep a copy
( umask 077; head -c 32 /dev/urandom > /etc/proxmox-gui/master.key.new )
chown root:proxmox-gui /etc/proxmox-gui/master.key.new; chmod 0440 /etc/proxmox-gui/master.key.new
cd /opt/proxmox-gui/current/backend
PROXMOX_GUI_COOKIE_SECURE=false /opt/proxmox-gui/current/.venv/bin/python -m app.tools.rotate_master_key \
    --db /var/lib/proxmox-gui/app.db --old /etc/proxmox-gui/master.key --new /etc/proxmox-gui/master.key.new
mv -f /etc/proxmox-gui/master.key.new /etc/proxmox-gui/master.key          # only after the tool printed "N secret(s) re-encrypted"
systemctl start proxmox-gui-api proxmox-gui-worker
```

The tool decrypts every value with the old key first and changes nothing if any fails.

## Publishing a release (fork owner)

1. Once: create a signing key and publish its public half in the repo —
   `ssh-keygen -t ed25519 -f ~/.ssh/proxmox-gui-release -C "proxmox-gui release"`; put
   `proxmox-gui-release namespaces="proxmox-gui-release" <the .pub line>` in `deploy/release-signers`; run `scripts/sync-signers.sh`; commit.
2. Tag and push: `git tag v0.7.0 && git push origin v0.7.0` → the `release` workflow builds everything in CI and uploads a **draft**.
3. Sign locally and publish: `scripts/release-sign.sh v0.7.0 --publish` (needs the `gh` CLI and your key).

CI never holds the signing key. To point a fresh fork at itself: `scripts/set-fork.sh <owner/repo>`.

## Layout of this directory

```
deploy/
├── install.sh              host-side installer / updater front-end / uninstaller (embeds the signer key)
├── release-signers         allowed_signers source for the embedded key (scripts/sync-signers.sh)
├── pins.env                Node/Python download URLs + SHA-256 (scripts/update-pins.sh)
├── host/                   runs on the PVE node: proxmox-gui-ssh-gate
├── host-lxc/               runs in the LXC as root: proxmox-gui-updater
├── lxc/                    bootstrap.sh, render-caddyfile.sh
├── scripts/                secret generators (master.key, jwt/pepper, setup token)
├── systemd/                hardened units (+ redis/caddy drop-ins, updater path/service)
├── caddy/Caddyfile.template
└── tests/                  shell test harness with simulated host tools (deploy/tests/run.sh)
```

Logs: `pct exec <ctid> -- journalctl -u proxmox-gui-api -u proxmox-gui-worker -u proxmox-gui-frontend -u caddy -f`.
