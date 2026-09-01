# CL.6 — Linux/systemd deployment

Companion to `docs/v1-process-supervision.md` (which stays the authority on the supervision
*policy*: restart/backoff/storm-breaker/clean-stop, all in `src/botgitgud/ops/supervisor.py`,
platform-independent). This document only covers the Linux-specific *launcher* — systemd instead
of Task Scheduler + PowerShell — and does not repeat policy details already documented there.

**Not covered here, on purpose** (out of scope for this ticket): creating the cloud VM, DuckDNS,
Caddy/reverse-proxy/TLS. Those come later, in the infrastructure ticket. This document only gets
the bot running locally on a Linux host under systemd, with the report server bound to loopback.

## Architecture

```
systemd
    -> python -m botgitgud.cli supervise   (the Python supervisor, ops/supervisor.py)
        -> python -m botgitgud.cli serve   (the bot child, spawned/restarted BY the supervisor)
            -> Discord runtime
            -> ReportServer 127.0.0.1:8080 (CL.3/CL.5, never exposed directly)
```

systemd supervises **only** the outer process (the Python supervisor). It never restarts the bot
child directly — that decision (backoff, storm-breaker, clean-stop via `control/stop.request`)
belongs entirely to `ops/supervisor.py`, exactly as it already does on Windows. Two competing
restart layers is exactly what this design avoids.

## 1. Create the service user

No home directory, no login shell — this account only ever runs one command:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin botgitgud
```

`--no-create-home` matters beyond hygiene: the systemd unit sets `ProtectHome=true`, which hides
`/home`, `/root`, and `/run/user/*` from the service. If this user's home directory lived under
`/opt/botgitgud`, `ProtectHome` would never touch it (it isn't one of those three paths) — but
giving the service account a home directory at all invites exactly that class of mistake later.
Keeping it homeless removes the question.

## 2. Copy the repository

```bash
sudo mkdir -p /opt/botgitgud
sudo chown botgitgud:botgitgud /opt/botgitgud
sudo -u botgitgud git clone <repo-url> /opt/botgitgud/repo   # or: rsync a deploy artifact in
```

Adjust the checkout layout to taste — the unit only cares that the final tree lands at
`/opt/botgitgud` with `pyproject.toml` at its root (see `WorkingDirectory` in the unit file).

## 3. Create the venv and install

```bash
cd /opt/botgitgud
sudo -u botgitgud python3 -m venv .venv
sudo -u botgitgud .venv/bin/pip install -e .
```

Any Python **3.12 or 3.13** on the host works — `pyproject.toml` declares `requires-python =
">=3.11"` and nothing in the codebase requires 3.14. Don't add a PPA or build Python from source
unless the distro's own package is genuinely older than 3.11; Ubuntu Server (current LTS) ships a
compatible `python3` already.

There is no `uv.lock` in this repository (deliberately out of scope for this ticket — see
`pyproject.toml`'s version ranges). This means a fresh install can, in principle, resolve slightly
different dependency versions on a different day. If that risk ever becomes a real incident,
introduce dependency pinning as its own ticket — don't reach for it here.

## 4. Create `.env`

```bash
sudo -u botgitgud cp .env.example .env
sudo -u botgitgud chmod 600 .env
sudo -u botgitgud "$EDITOR" .env   # fill in real credentials
```

At minimum, set real values for `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`,
`BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`. Also set:

```bash
DATA_DIR=/opt/botgitgud/data
```

An absolute path removes any dependency on the process's working directory at the moment
`Settings()` is read — belt-and-suspenders on top of the unit's own `WorkingDirectory=`.

`REPORT_PUBLIC_BASE_URL` stays **empty** at this stage (no VM, no DuckDNS, no Caddy yet) — leaving
it empty is intentional here, but note that `build_bot()` (CL.5) validates it at boot and fails
startup cleanly if it is ever empty/invalid *while link delivery is exercised*; there is no
production default baked in anywhere, by design (`config.py`).

Never commit a real `.env`. `.env.example` only ever holds placeholder/empty values.

## 5. Prepare `data/`

```bash
sudo -u botgitgud mkdir -p /opt/botgitgud/data/{logs,reports,raw,ops,control}
```

The service user needs read-write on the whole `data/` subtree — it's where the DuckDB warehouse,
persisted reports, the JSONL operational logs, and the `control/` marker files
(`stop.request`/`supervisor.lock`/`bot.pid`) all live. Nothing outside `data/` is ever written by
either process.

## 6. Install the systemd unit

```bash
sudo cp deploy/systemd/botgitgud.service /etc/systemd/system/botgitgud.service
sudo systemctl daemon-reload
sudo systemctl enable botgitgud
sudo systemctl start botgitgud
```

`deploy/systemd/botgitgud.service` is a **template**, versioned in the repo but never installed
automatically — copy it by hand, review the placeholder `User=`/`Group=`/paths against the real
host first if this deployment ever deviates from `/opt/botgitgud`.

## 7. Operate

```bash
sudo systemctl status botgitgud     # is it up, and since when
sudo systemctl restart botgitgud    # e.g. after an upgrade
sudo systemctl stop botgitgud       # graceful — see "Stop semantics" below
sudo journalctl -u botgitgud -f     # live systemd-captured stdout/stderr
```

The durable, structured trail stays in JSONL, not the journal — `journalctl` is for a quick "is it
alive / did it just crash" glance, not the source of truth:

```bash
tail -f /opt/botgitgud/data/logs/botgitgud.jsonl       # the bot (`serve`)
tail -f /opt/botgitgud/data/logs/supervisor.jsonl      # the supervisor
```

This ticket does **not** move logging to journald as the canonical trail — the rotating JSONL
sinks (`logging_setup.py`, B5) stay exactly as they are; systemd capturing stdout/stderr into the
journal is a free side benefit of running under systemd, not a replacement.

### Health check

```bash
curl http://127.0.0.1:8080/healthz
```

Local only — the report server is never bound to anything but loopback (`REPORT_SERVER_HOST`,
default `127.0.0.1`), and this unit opens no port. A future Caddy reverse-proxy talks to
`127.0.0.1:8080`; nothing in this ticket opens a firewall rule or exposes it externally. Don't wire
this into systemd's own watchdog/`ExecStartPost` polling — it's an operator's manual sanity check
in this ticket, not an automated liveness gate.

## Stop semantics — why `systemctl stop` doesn't leave a dangling process

```
systemctl stop
    -> SIGTERM to the supervisor's main PID (KillMode=mixed, see the unit's own comments)
    -> supervisor's signal handler writes control/stop.request
    -> bot's _stop_request_watcher (polling every BOT_STOP_POLL_INTERVAL_S) sees it
    -> bot calls close(): BotGitGudBot.close() stops ReportServer, then the Discord runtime
    -> bot process exits
    -> supervisor sees stop.request was already set, does NOT restart, exits 0
    -> systemd observes a clean (exit code 0) stop: Restart=on-failure does not fire
```

`TimeoutStopSec=45` in the unit gives this whole chain room to finish (default
`SUPERVISOR_STOP_GRACE_S=30` plus margin) before systemd would otherwise escalate to `SIGKILL`
across the whole cgroup. Raise both together if `SUPERVISOR_STOP_GRACE_S` is ever raised in `.env`.

A restart-storm giveup (the supervisor detects a crash loop and stops trying, see
`docs/v1-process-supervision.md`) **also** exits 0 — that's deliberate: it requires a human to
look at `supervisor.jsonl` and run `systemctl start botgitgud` again after fixing the underlying
cause, exactly like the Windows `.\start-bot-service.ps1` runbook already says. `systemd` correctly
leaves it alone either way.

## Upgrade procedure

```bash
sudo systemctl stop botgitgud
# backup first — see below
cd /opt/botgitgud
sudo -u botgitgud git fetch && sudo -u botgitgud git checkout <target-commit>
sudo -u botgitgud .venv/bin/pip install -e .
# run the project's own smoke/test suite here if the change is non-trivial
sudo systemctl start botgitgud
sudo systemctl status botgitgud
tail -20 /opt/botgitgud/data/logs/botgitgud.jsonl   # confirm process.started
```

Stop before touching anything under `/opt/botgitgud` — the DuckDB warehouse has a single-owner
contract (`docs/warehouse-policy.md`); never `pip install` or swap code while the bot still holds
the connection.

## Backup

Persistent state worth backing up:

```
data/warehouse.duckdb
data/raw/
data/reports/
data/ops/
data/logs/
```

**Never** back up or restore `data/control/stop.request` — it is an ephemeral control signal, not
operational state. Restoring it from a backup would immediately (and incorrectly) tell the running
supervisor to shut the bot down the moment it next polls. `ops/control.py`'s own contract already
guarantees the two processes that read it never delete it themselves (only
`start-bot-service.ps1`/its Linux equivalent clears it on a deliberate start) — a backup/restore
cycle must not reintroduce a stale one from outside that contract.

The free-tier architecture already decided the primary/secondary split: the always-on host is
primary, a local machine is the secondary copy. This document only names *what* to copy — it does
not implement a backup cron here; add one as its own ticket if/when it's actually needed.

## Filesystem ownership summary

```
/opt/botgitgud             repo + venv (code) — service user owns it, but nothing
                            outside data/ is written at runtime
/opt/botgitgud/data        persistent, writable state — warehouse, reports, logs, control/
```

A future Caddy does **not** need read access to any of this — it only ever reverse-proxies HTTP
requests to `127.0.0.1:8080`; it never touches DuckDB or the filesystem directly.

## Windows stays available

`scripts/*.ps1` and Task Scheduler remain fully functional and unmodified — this ticket adds a
Linux launcher, it does not retire the Windows one. See `docs/v1-process-supervision.md` for that
path.
