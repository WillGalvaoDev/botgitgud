# Linux/systemd deployment

Companion to `docs/v1-process-supervision.md` (the authority on the supervision *policy*:
restart/backoff/storm-breaker/clean-stop, all in `src/botgitgud/ops/supervisor.py`,
platform-independent) and to `docs/cl9b-activation-runbook.md` (the canonical step-by-step sequence
for turning a provisioned VM into a live public HTTPS endpoint, plus the external validation plan
and rollback procedure). This document covers the Linux *launcher* — systemd instead of Task
Scheduler + PowerShell — and the scripted deploy preparation around it.

## What is ready now vs. what waits for the VM

| Stage | Status | Where |
|---|---|---|
| Bootstrap, `.env` template, preflight, systemd install, backup/restore | **Ready and tested offline** (CL.8) | `deploy/` |
| Caddy reverse-proxy template, capability-token log/header contract | **Ready and tested offline** (CL.9A) | `deploy/caddy/`, `deploy/install-caddy.sh` |
| Activation runbook, domain↔`REPORT_PUBLIC_BASE_URL` coherence gate | **Ready and tested offline** (CL.9B) | `docs/cl9b-activation-runbook.md`, `deploy/activation-readiness.sh` |
| Provisioning the Oracle VM, SSH access | **Blocked on the VM existing** (CL.7) | manual, see `docs/` roadmap |
| Installing Caddy for real, binding 443/80, first TLS certificate | **Blocked on the VM existing** (CL.9A install step) | `deploy/install-caddy.sh`, run on the VM |
| DuckDNS hostname, DNS `A` record, opening 80/443 in OCI | **Blocked on the VM existing**, manual (CL.9B) | `docs/cl9b-activation-runbook.md` |
| External validation (DNS/ports/TLS/routes/headers/Discord) | **Blocked on the VM + DNS existing** — plan documented, not executed | `docs/cl9b-activation-runbook.md` |
| **First production start of the bot** | **Blocked until DNS + TLS are real** — see below | — |

> ### The first production start is blocked until CL.9B (DNS)
>
> Capability-link delivery (CL.5) requires a valid `REPORT_PUBLIC_BASE_URL`. `build_bot()`
> validates it at boot and fails loudly rather than emitting broken links — so starting the service
> without a real public domain produces a crash-loop and, on the fifth restart, a storm-breaker
> giveup. That domain does not exist until CL.9B (DuckDNS) points a hostname at the VM's public IP
> and Caddy obtains a real certificate for it.
>
> **What CL.8 + CL.9A deliver is a fully prepared host and reverse-proxy template, not a running
> bot or a live HTTPS endpoint.** Bootstrap, preflight, systemd install/`enable`, and the Caddy
> template/renderer are all ready and offline-tested; only the bot's `start` and Caddy's real
> installation/certificate issuance are gated on the VM and DNS existing. This is enforced in code,
> not just documented: `install-systemd.sh --start` validates with `--production`, which turns an
> empty `REPORT_PUBLIC_BASE_URL` into an error; `install-caddy.sh` refuses to run without an
> explicit `--domain`.
>
> Do **not** work around this by inventing a placeholder URL or domain. A wrong base URL produces
> reports whose links silently point nowhere — strictly worse than a service that has not started.

Everything under `deploy/` is exercised by the offline suite (`tests/unit/test_deploy_prep.py`,
`tests/unit/test_deploy_scripts.py`, `tests/unit/test_systemd_unit.py`,
`tests/unit/test_caddy_deploy.py`) and needs no network, no root, no systemd and no `caddy` binary
to be verified. The scripts themselves, of course, only *run* on the target host.

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
firewall or SSH, install Caddy, fetch code, or start the service.

### 2. Configure `.env`

```bash
sudo -u botgitgud cp deploy/env.example /opt/botgitgud/.env
sudo -u botgitgud chmod 600 /opt/botgitgud/.env
sudo -u botgitgud "$EDITOR" /opt/botgitgud/.env
```

`deploy/env.example` is the **production subset** — only the variables a deploy actually has to
decide. (The repo-root `.env.example` is the development reference that documents every `Settings`
field; it is verified by its own test. Two files, two audiences.)

Required: the five credentials, `DATA_DIR` (absolute), and the report-server host/port.
`REPORT_PUBLIC_BASE_URL` stays **empty** until a real domain exists — that is the honest state, and
`build_bot()` (CL.5) fails startup loudly rather than emitting broken links.

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
imports (including `aiohttp` via the report server), persistent directories, real write
permission, the systemd unit, `.env` validity, and whether the report-server port can actually be
bound on loopback. It binds and immediately closes a socket — never listens, never accepts, never
resolves an external name.

There are **two gates**, and they are not the same question:

| Mode | Command | `REPORT_PUBLIC_BASE_URL` empty |
|---|---|---|
| Host preparation (default) | `./deploy/preflight.sh` | warning — host can be ready before CL.9 |
| Production start | `... deploy-preflight --production` | **error** — the bot cannot serve links |

Warnings do not fail the run: a non-ARM64 dev machine and an empty `REPORT_PUBLIC_BASE_URL` are
both legitimate during host preparation. A **present** URL is validated by CL.5's own
`validate_report_public_base_url` in both modes — the flag only changes how an *absent* one is
treated, never weakening the existing validation.

### 4. Install the systemd unit

```bash
sudo ./deploy/install-systemd.sh            # validate -> copy -> daemon-reload -> enable
sudo ./deploy/install-systemd.sh --start    # same, then validate .env and start
```

Validates the unit (`systemd-analyze verify` when available, plus a check that `ExecStart` targets
the supervisor) **before** copying it to `/etc/systemd/system`.

It does not start the service by default. `--start` applies the **production** gate: a missing
credential *or* a missing `REPORT_PUBLIC_BASE_URL` refuses the start. Until CL.9 provides a real
domain, `--start` is therefore expected to refuse — that is the gate working, not a bug. Install
and `enable` still succeed, so the host is ready and the unit will be active on the next boot once
the URL is configured.

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
curl http://127.0.0.1:8080/healthz     # -> ok
```

Local only. The CL.3 HTTP contract is unchanged — `/healthz` returns `200 ok` and deliberately
exposes nothing internal. The report server never binds outside loopback, this unit opens no port,
and no firewall rule is created by anything in `deploy/`. A future Caddy will reverse-proxy to
`127.0.0.1:8080`; that is not implemented yet.

Do not wire this endpoint into a systemd watchdog or an aggressive polling loop — it is an
operator's manual sanity check at this stage.

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
`raw/`, `reports/`, `ops/`, `logs/`.

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
/opt/botgitgud/data        persistent, writable state — warehouse, reports, logs, control/
```

A future Caddy needs read access to none of it — it only reverse-proxies to `127.0.0.1:8080`.

## CL.9A — Public HTTPS ingress (Caddy)

```
Internet -> HTTPS :443 -> Caddy -> 127.0.0.1:8080 -> ReportServer (CL.3)
```

The `ReportServer` (`bot/report_server.py`) does not change: it still binds only to
`127.0.0.1:8080` (CL.3/CL.5, unmodified). Caddy is the one and only process this architecture
allows to face the Internet.

### OFFLINE AGORA (ready and tested without the VM)

- `deploy/caddy/Caddyfile.template` — reverse-proxy template. `reverse_proxy` targets exactly
  `127.0.0.1:8080`; only `path /r/*` is forwarded; everything else (`/healthz` included) falls
  through to a generic `respond 404`, deny-by-default. No `log` block, no `redir`, no
  `header_up`/`header_down`, no `tls internal` — see "Capability token and logs" below.
- `src/botgitgud/ops/caddy_config.py` — pure domain validation (`validate_caddy_domain`) and
  template rendering (`render_caddyfile`), the same "policy in Python, thin shell wrapper" split as
  CL.6/CL.8. Rejects an empty domain, the literal placeholder, a scheme/port/path/whitespace, and
  (found during this round's own review) enforces the placeholder appears **exactly once** in the
  template — a naive `str.replace` with two occurrences corrupted an explanatory comment the first
  time this was written; the renderer now refuses to render a template shaped like that.
- `deploy/install-caddy.sh` — validates before installing, never overwrites a differing config
  without `--force`, never reloads without `--reload`, never installs the `caddy` package itself
  without `--install-package` (the only path that touches the network), never starts
  `botgitgud.service`, never touches SSH or a firewall.
- `tests/unit/test_caddy_deploy.py` — the contract above as offline, parametrized guards.

### DEPENDE DA VM (blocked until CL.7's Oracle instance exists)

- Installing the real `caddy` binary (`install-caddy.sh --install-package`, needs network).
- Running `deploy-render-caddyfile` + `caddy validate` + installing to `/etc/caddy/Caddyfile` for
  real.
- Binding `:80`/`:443` for real traffic.

### DEPENDE DE DNS (CL.9B, not started here)

- A DuckDNS hostname (or equivalent) with an `A` record pointing at the VM's public IPv4.
- Caddy's first real ACME/Let's Encrypt certificate issuance (needs the domain to already resolve
  and port 80 reachable for the HTTP-01 challenge).
- Setting the final `REPORT_PUBLIC_BASE_URL=https://<real-domain>` in `/opt/botgitgud/.env` — see
  "Setting `REPORT_PUBLIC_BASE_URL`" below.
- The first production `systemctl start botgitgud`.

### Installing Caddy (future, on the VM)

```bash
sudo ./deploy/install-caddy.sh --domain example.duckdns.org --install-package
# review the diff shown, then, once satisfied:
sudo ./deploy/install-caddy.sh --domain example.duckdns.org --reload
```

Without `--domain`, the script refuses immediately — CL.9A never defaults to a fictional
production domain. Without `--install-package`, it skips `apt-get`/network entirely and just
renders + validates (when `caddy` is already present) + installs the config file; without
`--reload`, Caddy is never told to pick up the new config. Each flag is a deliberate, separate
gesture — never bundled into one "just do everything" invocation.

### Setting `REPORT_PUBLIC_BASE_URL`

Once — and only once — DNS resolves and Caddy has issued a real certificate:

```bash
sudo -u botgitgud "$EDITOR" /opt/botgitgud/.env
# REPORT_PUBLIC_BASE_URL=https://example.duckdns.org

/opt/botgitgud/.venv/bin/python -m botgitgud.cli deploy-validate-env \
    --env-file /opt/botgitgud/.env --production
sudo ./deploy/install-systemd.sh --start
```

CL.8's `--production` gate (`ops/deploy.py::validate_env_values`) remains the sole authority on
production readiness — CL.9A does not add a second gate or duplicate that validation. It only
makes the URL this gate demands actually reachable.

### Capability token and logs

`/r/{token}` carries a bearer capability in the path — anything that persists that path persists
the secret. Audited and enforced by the template:

| Concern | How it's handled |
|---|---|
| Caddy access log | No `log` block in the site. Access logging in Caddy is opt-in (confirmed against the official Caddyfile `log` directive reference): without that block, Caddy writes no per-request access-log entry for this site at all — not merely a redacted one. |
| Query-string / alternate routing | None added — `reverse_proxy` forwards the original request unmodified; no `rewrite` directive exists. |
| Redirects | None — a `redir` that echoed the path (e.g. a trailing-slash normalizer) could leak the token into a `Location` header or an intermediary's log; the template has no `redir` directive at all. |
| Headers | No `header_up`/`header_down` — the security headers `bot/report_server.py` already sets (`X-Content-Type-Options`, `Content-Security-Policy`, `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store`) pass through to the client unmodified; Caddy adds nothing that would override them. |
| `/healthz` | Not proxied at all — falls through to the generic `respond 404`, same as any unmatched path. The internal health check stays reachable only via `127.0.0.1:8080/healthz` (SSH), never through the public domain. |
| TLS | Caddy's automatic ACME/Let's Encrypt only — no `tls internal`, no manually-managed certificate file. |
| Our own code (`ReportServer`, `ReportLinkStore`) | Audited this round. `_handle_report`'s catch-all logs only `token_fingerprint` (`bot/report_server.py`). One real gap found and fixed: `ReportLinkStore.issue()`'s token-collision-exhausted path used to chain the raw `duckdb.ConstraintException` as the exception's `__cause__` — verified empirically that DuckDB embeds the literal offending value in that exception's message (`'Duplicate key "token: <full token>" ...'`), which `exc_info=True` logging would have printed in full. Fixed by raising `from None` instead of chaining it; see `bot/report_links.py` and the regression test in `test_report_links.py`. This path only triggers after five consecutive collisions of a 256-bit random token, but the fix costs nothing and closes a real (if astronomically unlikely) leak vector. |

### Threat model: Caddy's own error log — what we can and cannot guarantee

The access-log claim above is something we can prove: it's a static property of our own committed
Caddyfile, verified against official Caddy documentation. The following is a different, narrower
class of risk that we audited but **cannot** close with equal confidence, and we say so plainly
rather than asserting a guarantee we can't back up.

**What we confirmed (official docs + multiple independent real-world reports):** Caddy's HTTP
server has a separate error-level logger (`http.log.error`) that is active independent of whether a
site has an access `log` block configured. It fires when `reverse_proxy` cannot reach its upstream
— i.e. exactly the scenario where `127.0.0.1:8080` is unreachable because the bot process is down
(a normal, expected state during a deploy/restart window, not a rare edge case). This goes to
stderr, which under systemd lands in `journalctl -u caddy`.

**What we could not confirm either way:** whether that specific error-log entry includes the
request's URI (and therefore the capability token). Real-world examples we found are inconsistent:
some show a bare message with no request object at all (`{"logger":"http.log.error","msg":"dial
tcp ...: connection refused"}`); other Caddy error-logging code paths documented in the same source
tree do attach a structured request object (which includes a `uri` field) to error log entries in
other circumstances. We do not have a citable, version-pinned source that settles which shape
applies to a bare `reverse_proxy` dial failure in the Caddy version that will actually run on the
VM. **We are not going to claim a guarantee here we can't verify.**

**Precisely scoped, though**: this risk exists *only* while the upstream is unreachable. A live
`ReportServer` answering normally — 200, 404, or 410 — is proxied through transparently and hits
neither this error logger (which is for Caddy-level failures, not application HTTP status codes)
nor the access logger (disabled above). The exposure window is "bot is down and someone requests a
specific `/r/{token}` URL during that window," not "every request that ever reaches Caddy."

**No verified Caddyfile-only mitigation exists.** We looked for one and are not introducing it
without a documented directive to point to — the ticket that raised this explicitly forbids
inventing Caddy configuration. Silencing all error-level logging would also remove the "upstream
down" signal operators actually need, trading real observability for an unverified confidentiality
gain — not an acceptable trade per this project's own stated preference.

**The control we do have and already rely on**: capability tokens are time-bounded by CL.2
(`DEFAULT_REPORT_LINK_TTL_DAYS = 30`, `bot/report_links.py`). A worst-case appearance of a token in
Caddy's own journal has a known, bounded validity window — it is not a permanent secret. This is an
existing property of the system, not a new mechanism introduced to compensate for this finding.

**Operator guidance (documentation only, nothing automated by this ticket)**: treat the systemd
journal on the production host with the same handling discipline as any other secret-adjacent log
— `journalctl` access is root/`adm`-group-restricted by default on Ubuntu; if a log aggregator is
ever added downstream of the journal, confirm it does not forward `http.log.error` content for the
Caddy unit without first checking this specific risk.

### Firewall (documented only — nothing opened by this ticket)

| Port | Future policy |
|---|---|
| `22/tcp` | Restricted to the administrative IP, per current policy (unchanged by CL.9A). |
| `80/tcp` | Needed once Caddy is live, for the ACME HTTP-01 challenge and the plain-HTTP-to-HTTPS redirect Caddy manages automatically. |
| `443/tcp` | The public HTTPS endpoint. |
| `8080/tcp` | **Never public.** Loopback-only, exactly as CL.3/CL.5 already require — this ticket does not change that, and no script here opens it. |

No OCI Security List, `ufw`, or `iptables` rule is created by this ticket — provisioning the actual
firewall is CL.7/CL.9B's job, on the real VM.

## Windows stays available

`scripts/*.ps1` and Task Scheduler remain fully functional and unmodified — this is an additional
launcher, not a replacement. See `docs/v1-process-supervision.md`.
