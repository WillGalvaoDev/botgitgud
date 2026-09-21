"""CL.6 — guards for `deploy/systemd/botgitgud.service` and
`docs/linux-deployment.md`.

Nothing here starts systemd, a subprocess, or a real VM — this is a static
parse of the versioned unit file (via `configparser`, since a systemd unit
is close enough to INI for that to be safe here: no duplicate keys, no `%`
interpolation needed) plus plain-text assertions on the doc. Its whole job
is to prevent a future edit from silently reintroducing exactly the
mistakes this ticket was written to avoid: a second restart layer, a root
service, a leaked secret, or an exposed report-server port.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT_PATH = REPO_ROOT / "deploy" / "systemd" / "botgitgud.service"
DOC_PATH = REPO_ROOT / "docs" / "linux-deployment.md"

_SECRET_NAMES = (
    "DISCORD_TOKEN",
    "WCL_CLIENT_ID",
    "WCL_CLIENT_SECRET",
    "BLIZZARD_CLIENT_ID",
    "BLIZZARD_CLIENT_SECRET",
)


def _parse_unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # systemd allows repeated section headers to append; our unit doesn't
    # rely on that, and ConfigParser's default (last-wins, no duplicates)
    # is fine for the single-valued keys this file actually declares.
    parser.read(UNIT_PATH, encoding="utf-8")
    return parser


def test_unit_file_exists() -> None:
    assert UNIT_PATH.is_file()


def test_unit_is_parseable_as_ini_like_sections() -> None:
    parser = _parse_unit()
    assert {"Unit", "Service", "Install"} <= set(parser.sections())


def test_exec_start_points_at_the_supervisor() -> None:
    exec_start = _parse_unit()["Service"]["ExecStart"]
    assert "botgitgud.cli supervise" in exec_start


def test_exec_start_never_points_directly_at_the_bot() -> None:
    """Pointing ExecStart at `cli serve` would give systemd and the Python
    supervisor two competing restart policies over the same bot process.
    """
    exec_start = _parse_unit()["Service"]["ExecStart"]
    assert "botgitgud.cli serve" not in exec_start


def test_restart_policy_is_on_failure_not_always() -> None:
    """`Restart=always` would fight the storm-breaker (ops/supervisor.py
    exits 0 on a deliberate storm giveup) and turn a clean `systemctl stop`
    into an infinite restart loop.
    """
    service = _parse_unit()["Service"]
    assert service["Restart"] == "on-failure"


def test_does_not_run_as_root() -> None:
    service = _parse_unit()["Service"]
    assert service["User"].strip().lower() not in ("", "root")
    assert service["Group"].strip().lower() not in ("", "root")


def test_environment_file_is_defined_and_outside_the_repo_tree() -> None:
    service = _parse_unit()["Service"]
    env_file = service["EnvironmentFile"]
    assert env_file
    assert env_file.endswith(".env")


def test_unit_never_hardcodes_a_secret_value() -> None:
    """The unit may reference secret VAR NAMES (e.g. in a comment) but must
    never assign one a literal value directly — that's what EnvironmentFile
    is for.
    """
    text = UNIT_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, _value = stripped.partition("=")
        assert key.strip() not in _SECRET_NAMES, (
            f"unit file must not assign a literal value to {key.strip()!r}"
        )


def test_working_directory_matches_the_venv_prefix_used_by_exec_start() -> None:
    service = _parse_unit()["Service"]
    working_dir = service["WorkingDirectory"].rstrip("/")
    exec_start = service["ExecStart"]
    assert exec_start.startswith(working_dir + "/")


def test_service_has_no_inbound_socket_activation() -> None:
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "ListenStream" not in text
    assert "8080" not in text


def test_timeout_stop_sec_is_comfortably_above_the_default_supervisor_grace() -> None:
    """.env.example's SUPERVISOR_STOP_GRACE_S default is 30s (see that file) —
    TimeoutStopSec must leave systemd's own SIGKILL escalation strictly
    after the supervisor's own graceful-stop window, or the bot child never
    gets the chance to close its Discord and DuckDB connections.
    """
    service = _parse_unit()["Service"]
    timeout_stop_sec = int(service["TimeoutStopSec"])
    assert timeout_stop_sec > 30


def test_kill_mode_is_mixed_not_control_group() -> None:
    """`control-group` would SIGTERM the bot child directly (it has no
    signal handler — only the supervisor installs one), racing the
    supervisor's own control/stop.request protocol.
    """
    service = _parse_unit()["Service"]
    assert service["KillMode"] == "mixed"


def test_no_windows_path_appears_in_the_unit() -> None:
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "\\" not in text
    assert "Scripts" not in text
    assert ":\\" not in text


def test_unit_does_not_run_as_type_oneshot_or_notify_unexpectedly() -> None:
    """`serve`/`supervise` are long-running foreground loops — `simple` is
    the correct systemd `Type`; anything else would misrepresent the
    process's own lifecycle to systemd.
    """
    service = _parse_unit()["Service"]
    assert service["Type"] == "simple"


def test_windows_scripts_still_exist() -> None:
    scripts_dir = REPO_ROOT / "scripts"
    windows_scripts = {
        "bot-supervisor.ps1",
        "install-bot-service.ps1",
        "uninstall-bot-service.ps1",
        "start-bot-service.ps1",
        "stop-bot-service.ps1",
    }
    present = {p.name for p in scripts_dir.glob("*.ps1")}
    assert windows_scripts <= present


# -- docs/linux-deployment.md ------------------------------------------------


def test_deploy_doc_exists() -> None:
    assert DOC_PATH.is_file()


def test_deploy_doc_documents_no_inbound_http_surface() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "No inbound port" in text


def test_deploy_doc_documents_backup() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "backup" in text
    assert "warehouse.duckdb" in text


def test_deploy_doc_excludes_stop_request_from_backup() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "stop.request" in text
    # The exclusion must be stated explicitly, not just the filename
    # appearing somewhere else (e.g. the stop-semantics diagram) — "Never"
    # must sit close to an actual mention of stop.request, the real guard
    # against a future doc edit that loosens this without noticing.
    assert re.search(r"never[^\n]{0,80}stop\.request", text, re.IGNORECASE)


def test_deploy_doc_documents_stop_semantics() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "stop semantics" in text
    assert "sigterm" in text


def test_deploy_doc_documents_upgrade_procedure() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "upgrade" in text


def test_deploy_doc_does_not_configure_caddy_duckdns_or_a_cloud_vm() -> None:
    """CL.6's explicit boundary: infrastructure steps belong to a later
    ticket. Their names may appear only as an explicit "not covered here".
    """
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "not covered here" in text or "out of scope" in text
