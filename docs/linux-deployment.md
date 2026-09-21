# Linux/systemd deployment

Companion to `docs/v1-process-supervision.md` (the authority on the supervision *policy*:
restart/backoff/storm-breaker/clean-stop, all in `src/botgitgud/ops/supervisor.py`,
platform-independent). This document covers the Linux *launcher* — systemd instead of Task
Scheduler + PowerShell — and the scripted deploy preparation around it.

The bot exposes no inbound HTTP surface: completed analyses are delivered as one Discord coaching
response, without attachment or URL.

## What is ready now vs. what waits for the VM

| Stage | Status | Where |
|---|---|---|
| Bootstrap, `.env` template, preflight, systemd install, backup/restore | **Ready and tested offline** (CL.8) | `deploy/` |
| Provisioning the Oracle VM, SSH access | **Blocked on the VM existing** (CL.7) | manual, see `docs/` roadmap |
| **First production start of the bot** | **Ready once the VM and credentials exist** | `deploy/install-systemd.sh --start` |

Everything under `deploy/` is exercised by the offline suite (`tests/unit/test_deploy_prep.py`,
`tests/unit/test_deploy_scripts.py`, `tests/unit/test_systemd_unit.py`) and needs no network, no
root, and no systemd binary to be verified. The scripts themselves, of course, only *run* on the
target host.

Network and cloud provisioning are not covered here. The application needs outbound HTTPS for
Discord, Warcraft Logs and Blizzard. No inbound port, DNS record, TLS certificate or reverse proxy
is required.

## Architecture

```
systemd
    -> python -m botgitgud.cli supervise   (the Python supervisor, ops/supervisor.py)
        -> python -m botgitgud.cli serve   (the bot child, spawned/restarted BY the supervisor)
            -> Discord runtime
```

systemd supervises **only** the outer process (the Python supervisor). It never restarts the bot
child directly — that decision (backoff, storm-breaker, clean-stop via `control/stop.request`)
belongs entirely to `ops/supervisor.py`, exactly as it already does on Windows. Two competing
restart layers is exactly what this design avoids.

## Deploy flow

```
bootstrap  ->  configure .env  ->  preflight  ->  install systemd  ->  start
                                                                        |
                                            health check  <-------------+
                                                                        |
                                       stop / restart / backup / restore
```

### 0. Stage the source tree (operator, before bootstrap)

`bootstrap-linux.sh` does **not** fetch code — no `git clone`, no download, no network. Put the
tree at `/opt/botgitgud` first:

```bash
sudo mkdir -p /opt/botgitgud
sudo git clone <repo-url> /opt/botgitgud      # or: rsync -a ./ host:/opt/botgitgud
```

`/opt/botgitgud` must be the **repository root**. Bootstrap verifies four marker files
(`pyproject.toml`, `src/botgitgud/__init__.py`, `src/botgitgud/cli.py`,
`deploy/systemd/botgitgud.service`) and refuses with an actionable message if any is missing — an
interrupted rsync or a wrong checkout fails there, cheaply, instead of halfway through `pip
install`.

### 1. Bootstrap the host

```bash
sudo ./deploy/bootstrap-linux.sh            # add --with-apt to let it install system packages
sudo ./deploy/bootstrap-linux.sh --repo-dir /srv/botgitgud   # non-default root
```

Idempotent. Creates the `botgitgud` system user/group (no home, no login shell — paired with
`ProtectHome=true` in the unit), creates `/opt/botgitgud/data` and every persistent subdirectory,
sets ownership and permissions (`750` on data, `600` on `.env` if present), verifies the minimum
system dependencies, creates `.venv`, and installs the package.

The install target is the **explicit path** (`pip install -e "${REPO_DIR}"`), never `-e .` — a `.`
would resolve against the caller's working directory, so a `sudo` from elsewhere would install a
different tree, or fail without saying why. After installing, bootstrap asserts that
`botgitgud.__file__` actually resolves back to `${REPO_DIR}`, which catches a venv carrying an
older editable install pointing at another checkout.

It deliberately does **not**: write any secret, invent `.env` values, open a port, touch the
firewall or SSH, fetch code, or start the service.

### 2. Configure `.env`

```bash
sudo -u botgitgud cp deploy/env.example /opt/botgitgud/.env
sudo -u botgitgud chmod 600 /opt/botgitgud/.env
sudo -u botgitgud "$EDITOR" /opt/botgitgud/.env
```

`deploy/env.example` is the **production subset** — only the variables a deploy actually has to
decide. (The repo-root `.env.example` is the development reference that documents every `Settings`
field; it is verified by its own test. Two files, two audiences.)

Required: the five credentials and an absolute `DATA_DIR`.

The validator refuses a template that was copied but never filled:

```bash
/opt/botgitgud/.venv/bin/python -m botgitgud.cli deploy-validate-env \
    --env-file /opt/botgitgud/.env
```

It prints variable **names** and diagnostics only — never a value. That is a structural property of
the code (`EnvIssue` has nowhere to put one), not a formatting convention, so its output is safe in
a shared terminal, a log, or CI.

### 3. Preflight

```bash
./deploy/preflight.sh
```

Offline checks, exit code `!= 0` on any failure: architecture, Python version, venv, package
imports, persistent directories, real write permission, the systemd unit, and `.env` validity.
It never listens on a socket and never resolves an external name.

Warnings do not fail the run: a non-ARM64 development machine is legitimate during host
preparation. Credential and filesystem errors remain failures.

### 4. Install the systemd unit

```bash
sudo ./deploy/install-systemd.sh            # validate -> copy -> daemon-reload -> enable
sudo ./deploy/install-systemd.sh --start    # same, then validate .env and start
```

Validates the unit (`systemd-analyze verify` when available, plus a check that `ExecStart` targets
the supervisor) **before** copying it to `/etc/systemd/system`.

It does not start the service by default. `--start` validates the environment first: a missing
credential refuses the start. Install and `enable` still succeed independently, so the host can be
prepared before credentials are provisioned.

### 5. Operate

```bash
sudo systemctl status botgitgud
sudo systemctl restart botgitgud
sudo systemctl stop botgitgud
sudo journalctl -u botgitgud -f
```

The durable structured trail stays in JSONL — `journalctl` is for a quick "is it alive" glance:

```bash
tail -f /opt/botgitgud/data/logs/botgitgud.jsonl       # the bot (`serve`)
tail -f /opt/botgitgud/data/logs/supervisor.jsonl      # the supervisor
```

### 6. Health check

```bash
/opt/botgitgud/.venv/bin/python -m botgitgud.cli ops-status \
    --data-dir /opt/botgitgud/data
```

This reads the local operational snapshot and reports worker/supervisor state without opening or
probing an inbound HTTP endpoint. Pair it with `systemctl status botgitgud` and the structured logs
above when diagnosing startup or worker progress. Do not wire it into an aggressive polling loop;
it is an operator's manual sanity check.

## Stop semantics — why `systemctl stop` doesn't leave a dangling process

```
systemctl stop
    -> SIGTERM to the supervisor's main PID (KillMode=mixed, see the unit's own comments)
    -> supervisor's signal handler writes control/stop.request
    -> bot's _stop_request_watcher (polling every BOT_STOP_POLL_INTERVAL_S) sees it
    -> bot calls close(): BotGitGudBot.close() stops the Discord runtime
    -> bot process exits
    -> supervisor sees stop.request was already set, does NOT restart, exits 0
    -> systemd observes a clean (exit code 0) stop: Restart=on-failure does not fire
```

`TimeoutStopSec=45` gives this chain room to finish (default `SUPERVISOR_STOP_GRACE_S=30` plus
margin) before systemd would escalate to `SIGKILL` across the cgroup. Raise both together if the
grace period is ever raised.

A restart-storm giveup **also** exits 0 — deliberately: it requires a human to read
`supervisor.jsonl` and run `systemctl start botgitgud` after fixing the cause. systemd correctly
leaves both cases alone.

## Backup

**Safe sequence once the service is operational — stop, back up, start:**

```bash
sudo systemctl stop botgitgud
./deploy/backup.sh                                    # -> /opt/botgitgud/backups/*.tar.gz
sudo systemctl start botgitgud
```

Other forms:

```bash
./deploy/backup.sh --dest /mnt/external/botgitgud
/opt/botgitgud/.venv/bin/python -m botgitgud.cli deploy-list-backups --dest /opt/botgitgud/backups
```

`backup.sh` **refuses to run** while `botgitgud.service` is active, exiting non-zero. DuckDB has a
single-owner contract: an archive taken mid-write can capture the warehouse in an intermediate
state — a file that looks valid and only fails when someone actually needs it. A consistent hot
backup is a different problem, deliberately out of scope for this phase.

The script never stops the service itself — that is the operator's decision. Where systemd does not
exist (development machine, CI) the guard is skipped, so backup stays testable offline.

Produces a local `.tar.gz`. No S3, no OCI Object Storage, no paid service — copying the archive
elsewhere afterwards is the operator's decision.

**Included** (allowlist in `src/botgitgud/ops/deploy.py::BACKUP_ENTRIES`): `warehouse.duckdb`,
`raw/`, historical `reports/` when present, `ops/`, `logs/`.

**Excluded**, and why:

| Excluded | Reason |
|---|---|
| `control/` (whole directory) | Ephemeral control state, **never** backed up and never restored: a restored `stop.request` would tell the supervisor to shut down on its next poll, and a stale `bot.pid`/`supervisor.lock` would confuse boot-time recovery. |
| `ops-snapshot.json` | Republished by the running bot within seconds; never a source of truth. |
| `spells.json` | Runtime cache, reseeded from the repository. |
| venv, source code, `.env` | Not state. The allowlist means these cannot leak into an archive that later gets copied around. |

The allowlist is the point: a denylist would silently include any new directory added under
`data/` later. Here the failure mode is "something was left out of the backup" (detectable,
fixable), never "a credential ended up inside it".

## Restore

```bash
sudo systemctl stop botgitgud
./deploy/restore.sh /opt/botgitgud/backups/botgitgud-backup-20260901-120000.tar.gz
./deploy/restore.sh <archive> --overwrite      # required to replace existing files
sudo systemctl start botgitgud
```

The service must be stopped — replacing the warehouse file under an open DuckDB connection
corrupts it, and `restore.sh` refuses while the unit is active. Restore also refuses to overwrite
existing files without `--overwrite`, because restoring over a live warehouse is only recoverable
with another backup.

**Every member is validated before a single byte is extracted.** A backup archive is an artifact
that circulates and can be substituted; that it is *normally* produced by this program is no
guarantee, and that is precisely the threat model of a restore. Rejected outright:

- absolute paths (`/etc/...`, `C:/...`);
- any `..` component, including under the `data/` prefix;
- backslashes — never present in a legitimate tar member name, and a path separator on Windows, so
  `data/..\..\evil` would pass a POSIX-component check and escape on extraction;
- symlinks and hardlinks (the classic escape vector), devices and FIFOs;
- anything outside the `data/` prefix or outside the backup allowlist.

One hostile member rejects the **whole** archive, so a partial extraction never lands on disk.
`filter="data"` (stdlib) stays applied on top of this as defense in depth.

## Upgrade procedure

```bash
sudo systemctl stop botgitgud
./deploy/backup.sh
cd /opt/botgitgud
sudo -u botgitgud git fetch && sudo -u botgitgud git checkout <target-commit>
sudo -u botgitgud .venv/bin/pip install -e .
./deploy/preflight.sh
sudo systemctl start botgitgud
tail -20 /opt/botgitgud/data/logs/botgitgud.jsonl   # confirm process.started
```

Always stop before touching `/opt/botgitgud` — single-owner warehouse (`docs/warehouse-policy.md`).

## Filesystem ownership

```
/opt/botgitgud             repo + venv (code) — owned by the service user; nothing outside
                            data/ is written at runtime
/opt/botgitgud/data        persistent, writable state — warehouse, logs, control/
```

## Windows stays available

`scripts/*.ps1` and Task Scheduler remain fully functional and unmodified — this is an additional
launcher, not a replacement. See `docs/v1-process-supervision.md`.
