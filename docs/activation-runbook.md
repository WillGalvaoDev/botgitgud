# CL.9B — Public Report Activation & External Validation Runbook

Companion to `docs/linux-deployment.md` (CL.6/CL.8: bootstrap, `.env`, preflight, systemd) and its
own "CL.9A — Public HTTPS ingress" section (Caddy template, `install-caddy.sh`). This document is
the **canonical sequence** that turns an already-provisioned VM into a working public HTTPS
endpoint, plus the external validation plan and rollback procedure for when that happens for real.

**Nothing in this document has been executed.** No Oracle VM exists yet (CL.7). No DNS record
exists. No port has been opened. No ACME challenge has run. This is preparation and a runbook, not
a report of an activation that already happened.

## Mutable vs. validation-only

Every step below is tagged:

- **[MUTABLE]** — changes real state (DNS, OCI, systemd, Caddy, the bot process). Only ever run by
  an operator, by hand, deliberately.
- **[VALIDATE]** — reads local state or (once the VM exists) makes a read-only external check.
  Several of these are automated by `botgitgud.cli deploy-activation-readiness` (offline ones) or
  are manual `curl`/`dig` commands (once DNS/TLS exist — never automated by this ticket).

## The canonical sequence

| # | Step | Kind | Where |
|---|---|---|---|
| 1 | VM provisioned (Oracle A1, Ubuntu 24.04 ARM64) | [MUTABLE] | CL.7, manual, out of scope here |
| 2 | Validate host (`uname -m`, `python3 --version`, `nproc`, `free -h`) | [VALIDATE] | manual SSH, CL.7 |
| 3 | Stage the source tree at `/opt/botgitgud` (`git clone`/rsync) | [MUTABLE] | `docs/linux-deployment.md` §0 |
| 4 | Bootstrap (`bootstrap-linux.sh`) — user/group, dirs, venv, install | [MUTABLE] | CL.8 |
| 5 | Configure `.env` — real credentials, `REPORT_PUBLIC_BASE_URL` still empty | [MUTABLE] | `deploy/env.example` |
| 6 | Preflight host (`deploy-preflight`, host-preparation mode) | [VALIDATE] | CL.8, automated |
| 7 | Install/`enable` systemd unit, **never** `--start` yet | [MUTABLE] | CL.8 |
| 8 | Install Caddy package (`install-caddy.sh --install-package`) | [MUTABLE] | CL.9A |
| 9 | Configure DNS (DuckDNS `A` record → VM's public IPv4) | [MUTABLE] | **CL.9B, manual — see below** |
| 10 | Validate DNS resolves correctly | [VALIDATE] | manual `dig`/`nslookup`, see §External validation A |
| 11 | Open **only** 80/443 in the OCI Security List | [MUTABLE] | manual OCI console, out of scope for automation |
| 12 | Validate `8080` is still not publicly reachable | [VALIDATE] | see §External validation B |
| 13 | Render + validate the Caddyfile (`deploy-render-caddyfile`, `caddy validate`) | [VALIDATE]/[MUTABLE]† | CL.9A |
| 14 | Activate Caddy (`install-caddy.sh --domain ... --reload`, or first `systemctl start caddy`) | [MUTABLE] | CL.9A |
| 15 | Wait for / validate the ACME certificate | [VALIDATE] | see §External validation C |
| 16 | Set `REPORT_PUBLIC_BASE_URL=https://<domain>` in `.env` | [MUTABLE] | this document, §Setting the URL |
| 17 | `deploy-activation-readiness` — the full offline gate, including domain↔URL coherence | [VALIDATE] | **CL.9B, automated** |
| 18 | `deploy-preflight --production` | [VALIDATE] | CL.8, automated |
| 19 | Start the bot (`systemctl start botgitgud`) | [MUTABLE] | CL.8 |
| 20 | Validate local health (`127.0.0.1:8080/healthz`) | [VALIDATE] | CL.3, manual SSH |
| 21 | Issue one test capability, test the public HTTPS endpoint end-to-end | [VALIDATE] | see §External validation D/E — **only with explicit authorization** |
| 22 | Validate a real Discord delivery (compact message, one link, zero attachment) | [VALIDATE] | see §External validation F — **only when explicitly authorized to test against real Discord** |
| 23 | Finalize activation (update runbook status, confirm rollback plan is current) | [VALIDATE] | this document |

† Step 13 is [VALIDATE] for the render/`caddy validate` sub-steps and [MUTABLE] only for the
`install -m 0644` file copy — `install-caddy.sh` already keeps these as separate, explicit
sub-steps (see `docs/linux-deployment.md`'s CL.9A section).

Step 21 (issuing a real capability) and step 22 (a real Discord test) are **not** performed as part
of this ticket, and this runbook does not authorize them on its own — they require a separate,
explicit go-ahead when the VM and DNS actually exist. Nothing here schedules or implies that
authorization.

### Configuring DNS (step 9 — manual, CL.9B)

Not automated by this or any prior ticket. On DuckDNS (or equivalent):

1. Create/confirm the subdomain (e.g. `botgitgud.duckdns.org`).
2. Point its `A` record at the VM's public IPv4 (visible in the OCI console after CL.7).
3. Do **not** script the DuckDNS update-token call as part of this repository — that is a manual,
   one-time console action, or a future ticket's job if recurring dynamic-DNS updates are ever
   needed (this VM's IP is expected to be static once assigned).

### Setting `REPORT_PUBLIC_BASE_URL` (step 16)

```bash
sudo -u botgitgud "$EDITOR" /opt/botgitgud/.env
# REPORT_PUBLIC_BASE_URL=https://botgitgud.duckdns.org
```

Must be **the same host** as the domain configured in the Caddyfile — step 17's
`deploy-activation-readiness` gate exists specifically to catch a mismatch here before it becomes a
production incident (see §Domain ↔ `REPORT_PUBLIC_BASE_URL` coherence below).

## Automated offline gate: `deploy-activation-readiness`

```bash
./deploy/activation-readiness.sh --domain botgitgud.duckdns.org --caddyfile /etc/caddy/Caddyfile
# or directly:
/opt/botgitgud/.venv/bin/python -m botgitgud.cli deploy-activation-readiness \
    --domain botgitgud.duckdns.org --caddyfile /etc/caddy/Caddyfile
```

Aggregates, in one report, exit code `!= 0` on any failure:

- Everything `deploy-preflight --production` already checks (CL.8): architecture, Python, venv,
  package imports, persistent directories, write permission, the systemd unit, full `.env`
  validity **in production mode** (`REPORT_PUBLIC_BASE_URL` required, not just a warning), and
  whether the report-server port can be bound on loopback.
- **Source staging** — the same four marker files `bootstrap-linux.sh` checks in shell, re-checked
  here in Python as part of activation (the two checks protect the same invariant at two different
  stages of the flow, not one check running twice by accident).
- **Domain syntax** — `ops/caddy_config.py::validate_caddy_domain` (CL.9A), reused, not
  reimplemented.
- **Caddyfile contract** — `ops/caddy_config.py::caddyfile_contract_violations`, the same contract
  `tests/unit/test_caddy_deploy.py` already proves against the *template*, generalized to also run
  against the **rendered** file with a real domain: exactly one `reverse_proxy` targeting
  `127.0.0.1:8080`, no `0.0.0.0`, `/healthz` never routed by a directive, no `log` block, no
  `redir`, and the deny-by-default `respond 404` present.
- **Domain ↔ `REPORT_PUBLIC_BASE_URL` coherence** — new this round, see next section.

This command is 100% offline: it never resolves DNS, never opens a socket beyond the same
bind-and-immediately-close probe CL.8's preflight already does on loopback, never calls
`systemctl`, never touches a firewall, never emits a capability link, never talks to Discord or
WCL. `ops/activation.py`'s own module docstring states this contract explicitly, and
`tests/unit/test_activation_readiness.py` proves it (running the check twice over the same
directory and asserting nothing new appears in it).

### Domain ↔ `REPORT_PUBLIC_BASE_URL` coherence

The exact operational error this gate exists to catch:

```
Caddy:  botgitgud.duckdns.org
.env:   REPORT_PUBLIC_BASE_URL=https://another-name.duckdns.org
```

Both pass their own individual validation (each is a syntactically valid domain / URL), but
together they'd produce a bot issuing links to a host Caddy never proxies. `ops/activation.py`'s
`validate_domain_matches_public_base_url` reuses `validate_caddy_domain` (CL.9A) and
`validate_report_public_base_url` (CL.5) for their own rules, and adds exactly three new checks
that only make sense in this specific context:

1. **Domain match** — the URL's host must equal the Caddy domain (case-insensitive).
2. **HTTPS only** — `validate_report_public_base_url` intentionally still accepts `http://` (it has
   to, for `http://127.0.0.1:<port>` local tests); a real activation against a public domain never
   should, so this gate adds that restriction on top, without touching the CL.5 function itself.
3. **No custom port** — production HTTPS is always port 443, managed by Caddy; a base URL like
   `https://domain:8443` is rejected here even though the bare URL shape is otherwise valid.

Path, query, and fragment rejections are **not** reimplemented — they come from
`validate_report_public_base_url` already raising before this gate's own checks even run.

## Security operational rules

This runbook — and any manual session following it — **must never**:

| Rule | Why |
|---|---|
| Paste a token/secret into a shell command when an env-file or stdin alternative exists | Command-line arguments land in shell history and `ps` output for other users on the box. |
| `cat .env`, `echo $DISCORD_TOKEN`, or otherwise print `.env`'s contents | The whole point of `deploy-validate-env`/`deploy-preflight` is to report *validity* without ever printing a *value* — use those, not a raw read. |
| Put a capability token in a log line, issue tracker comment, or chat message | It's a bearer secret — anyone who reads it can open that report. |
| Test `/r/{token}` with `curl -v`/`--trace`/`--trace-ascii` piped to a persisted file, or with browser devtools "copy as cURL" saved to a shared location | These persist the full URL (token included) somewhere outside the operator's own terminal scrollback. |
| Make `REPORT_SERVER_HOST`/`8080` reachable from outside loopback, for "just a quick test" | This is the single invariant the entire CL.3–CL.9 architecture is built to protect; there is no test that requires it. |
| Enable Caddy access logging (`log` block) "temporarily to debug" | The default access-log format includes the full request line — see CL.9A's threat-model section in `docs/linux-deployment.md`. If traffic-level debugging is ever truly needed, do it without a token-bearing URL in flight, or use `curl -v` against a throwaway `/healthz` request. |
| Disable TLS certificate validation (`curl -k`/`curl --insecure`, `verify=False`, etc.) | If TLS doesn't validate, the site isn't actually correctly deployed yet — fix the certificate, don't stop checking for it. |
| Disable SSH host-key checking (`StrictHostKeyChecking=no`, `-o UserKnownHostsFile=/dev/null`) | Removes the only defense against a silently swapped remote host. If the known_hosts entry is genuinely stale (VM rebuilt), remove that *one* stale entry deliberately — never disable the check globally. |
| Invent a new secret-storage mechanism for this ticket | Not needed — `.env` (CL.8) and the capability-token warehouse table (CL.2) already exist and are unchanged by CL.9B. |

`tests/unit/test_activation_readiness.py` and `tests/unit/test_caddy_deploy.py` (CL.9A) enforce the
parts of this table that are properties of *our own scripts/docs* (no `curl -k` recommended
anywhere, no `StrictHostKeyChecking=no`, `/healthz` and `8080` documented as private). The
human-behavior rules (don't paste a token into chat) can't be enforced by a test — they're stated
here as the explicit, written policy for whoever runs the activation by hand.

## External validation plan (documented now, executed only once the VM exists)

None of this runs today. This is the exact, precise plan for when it can.

### A. DNS

```bash
dig +short botgitgud.duckdns.org
```

Expected: exactly the VM's public IPv4 — the same address shown in the OCI console for that
instance, nothing else.

### B. Ports

```bash
nc -zv botgitgud.duckdns.org 443   # expected: succeeds
nc -zv botgitgud.duckdns.org 80    # expected: succeeds (ACME challenge / redirect)
nc -zv botgitgud.duckdns.org 8080  # expected: FAILS — connection refused/timeout
```

The third check is the one that matters most: it is the external proof that the OCI Security List
opened in step 11 did **not** also open `8080`, and that the ReportServer's loopback bind (CL.3,
unchanged) holds up from outside the VM, not just in our own static config checks.

### C. TLS

```bash
curl -v https://botgitgud.duckdns.org/healthz 2>&1 | grep -E "subject:|SSL certificate verify"
```

Never `-k`/`--insecure`. Expected: certificate verifies, `subject:` matches the domain, connection
succeeds without any override.

### D. Routes

| Request | Expected |
|---|---|
| `GET /healthz` (public) | `404` — never proxied, see CL.9A |
| `GET /some-nonexistent-route` | `404` |
| `GET /r/<valid-token>` | `200`, HTML body |
| `GET /r/<invalid-token>` | `404` |
| `GET /r/<expired-token>` | `410` |

The valid/expired-token cases require a real capability to have been issued (step 21) — done only
with explicit authorization, never as a routine part of this validation pass, and the token used
must be deliberately short-lived / discarded immediately after the check.

### E. Headers on a valid report response

```bash
curl -sI https://botgitgud.duckdns.org/r/<valid-token>
```

Expected, matching the CL.3 contract exactly (`bot/report_server.py::_SECURITY_HEADERS`,
unchanged):

```
Content-Type: text/html; charset=utf-8
X-Content-Type-Options: nosniff
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'
Referrer-Policy: no-referrer
Cache-Control: private, no-store
```

### F. Discord

Exactly one compact message, ≤1800 characters, containing exactly one `https://` link, zero HTML
attachments — the CL.4/CL.5 product contract, unchanged. **Only tested when explicitly authorized
to exercise real Discord** — this runbook does not grant that authorization on its own, and no
automated test in this repository ever sends a real Discord message (see the existing
`tests/unit/test_delivery.py`/`test_discord_bot.py` fakes for why: real Discord calls are
categorically excluded from the offline suite).

## Rollback

If activation fails partway, or needs to be reversed after going live:

1. **Stop only the affected component** — if the bot is misbehaving, `systemctl stop botgitgud`;
   if Caddy/TLS is the problem, `systemctl stop caddy`. Don't stop both reflexively if only one is
   actually broken — an unaffected component staying up preserves more information about where the
   real fault is.
2. **Never delete `warehouse.duckdb`, `data/raw/`, or `data/reports/`** to "start clean." A broken
   activation is a configuration problem, not a data problem — see `docs/linux-deployment.md`'s
   backup/restore section if a restore is ever genuinely warranted (it almost never is for an
   activation-layer failure).
3. **Never revoke or delete capability-link rows** as part of rollback. `report_links` state is
   independent of Caddy/DNS/TLS; touching it doesn't fix an ingress problem and only forces
   legitimate, already-delivered links to break early.
4. **Preserve logs, but never print/forward a capability token while doing so.** `journalctl -u
   caddy`/`-u botgitgud` and the JSONL trail (`data/logs/`) are exactly what's needed to diagnose
   what went wrong — collect them, but see the logging caveat in `docs/linux-deployment.md`'s
   CL.9A section before forwarding raw Caddy error logs anywhere outside the host.
5. **Restore the previous Caddy config from backup when one exists.** `install-caddy.sh` refuses to
   overwrite a differing config without `--force`, and — when `--force` was used — the file it
   overwrote is recoverable from whatever the operator's own pre-change copy/version-control
   discipline preserved; this repo does not currently version a "last known good"
   `/etc/caddy/Caddyfile` snapshot automatically. If none exists, re-render from
   `deploy/caddy/Caddyfile.template` with the known-good domain and re-validate.
6. **Never "fix" an ingress failure by exposing `8080`.** Whatever the failure mode — Caddy down,
   cert expired, DNS wrong — routing around Caddy by making the ReportServer reachable directly is
   never an acceptable resolution. If the public endpoint must be temporarily unavailable while a
   real fix is worked out, that is the correct state — an outage, not an exposure.
7. **Re-run `deploy-activation-readiness` before attempting activation again.** It is the same gate
   that should have caught most configuration-shaped failures before this rollback was needed; a
   second attempt without re-running it risks repeating the same mistake.

## FORA DE ESCOPO (this ticket)

Not executed, not automated, not implied to have happened: creating the Oracle VM; configuring
DuckDNS for real; opening any port; running ACME; starting production; any external network call of
any kind; any commit. This document and the code it describes are preparation only.
