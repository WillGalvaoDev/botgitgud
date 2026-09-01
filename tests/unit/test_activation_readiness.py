"""CL.9B — activation readiness: propriedades que podemos provar offline.

Nada aqui toca DNS, OCI, firewall, systemd real, ou faz uma chamada HTTP.
`check_report_server_bind` (herdado de `ops/preflight.py` via
`run_activation_readiness`) já faz bind+close imediato em loopback — o
mesmo que a suíte de CL.8 já exercita, nada novo. Não criamos teste
fingindo provar DNS/TLS/OCI reais: cada teste aqui verifica uma
propriedade ESTÁTICA/local (parsing de string, comparação de valores,
existência de arquivo) que realmente é o que o código faz.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from botgitgud.ops.activation import (
    SOURCE_STAGING_MARKERS,
    ActivationConfigError,
    check_caddyfile_contract,
    check_domain_matches_public_base_url,
    check_source_staging,
    run_activation_readiness,
    validate_domain_matches_public_base_url,
)
from botgitgud.ops.caddy_config import render_caddyfile
from botgitgud.ops.deploy import PERSISTENT_DIRNAMES
from botgitgud.ops.preflight import CheckStatus, exit_code_for

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "deploy" / "caddy" / "Caddyfile.template"
SCRIPT_PATH = REPO_ROOT / "deploy" / "activation-readiness.sh"
RUNBOOK_PATH = REPO_ROOT / "docs" / "activation-runbook.md"

_VALID_ENV = {
    "DISCORD_TOKEN": "test-discord-value",
    "WCL_CLIENT_ID": "test-wcl-id",
    "WCL_CLIENT_SECRET": "test-wcl-secret",
    "BLIZZARD_CLIENT_ID": "test-bnet-id",
    "BLIZZARD_CLIENT_SECRET": "test-bnet-secret",
    "DATA_DIR": "/opt/botgitgud/data",
    "REPORT_SERVER_HOST": "127.0.0.1",
    "REPORT_SERVER_PORT": "8080",
    "REPORT_PUBLIC_BASE_URL": "https://foo.duckdns.org",
}


def _write_env(path: Path, **overrides: str) -> Path:
    values = dict(_VALID_ENV)
    values.update(overrides)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def _populate_data_dir(root: Path) -> None:
    for name in PERSISTENT_DIRNAMES:
        (root / name).mkdir(parents=True, exist_ok=True)


def _render(domain: str, tmp_path: Path) -> Path:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), domain)
    path = tmp_path / "Caddyfile.rendered"
    path.write_text(rendered, encoding="utf-8")
    return path


# -- coerência domínio ↔ REPORT_PUBLIC_BASE_URL ---------------------------------


def test_matching_domain_and_url_is_accepted() -> None:
    assert (
        validate_domain_matches_public_base_url("foo.duckdns.org", "https://foo.duckdns.org")
        == "https://foo.duckdns.org"
    )


def test_mismatched_domain_and_url_is_rejected() -> None:
    """O erro operacional exato descrito no ticket: Caddy=foo, .env=bar."""
    with pytest.raises(ActivationConfigError, match="não corresponde"):
        validate_domain_matches_public_base_url("foo.duckdns.org", "https://bar.duckdns.org")


def test_case_insensitive_domain_match_is_accepted() -> None:
    assert validate_domain_matches_public_base_url("Foo.DuckDNS.org", "https://foo.duckdns.org")


def test_http_scheme_is_rejected_for_activation() -> None:
    """CL.5 aceita http:// para testes locais em loopback; a ativação de
    produção real, com um domínio público, nunca aceita.
    """
    with pytest.raises(ActivationConfigError, match="https"):
        validate_domain_matches_public_base_url("foo.duckdns.org", "http://foo.duckdns.org")


def test_custom_port_is_rejected_for_activation() -> None:
    with pytest.raises(ActivationConfigError, match="porta"):
        validate_domain_matches_public_base_url("foo.duckdns.org", "https://foo.duckdns.org:8443")


def test_path_is_rejected_reusing_cl5_validation() -> None:
    """Reusa `validate_report_public_base_url` (CL.5) — não uma segunda
    regra divergente escrita aqui.
    """
    with pytest.raises(Exception):  # noqa: B017 - a mesma exceção da CL.5
        validate_domain_matches_public_base_url(
            "foo.duckdns.org", "https://foo.duckdns.org/r/token"
        )


def test_query_is_rejected_reusing_cl5_validation() -> None:
    with pytest.raises(Exception):  # noqa: B017
        validate_domain_matches_public_base_url("foo.duckdns.org", "https://foo.duckdns.org?x=1")


def test_fragment_is_rejected_reusing_cl5_validation() -> None:
    with pytest.raises(Exception):  # noqa: B017
        validate_domain_matches_public_base_url("foo.duckdns.org", "https://foo.duckdns.org#frag")


def test_invalid_hostname_is_rejected_reusing_cl9a_validation() -> None:
    """Reusa `validate_caddy_domain` (CL.9A) — não uma segunda regex de
    domínio escrita aqui.
    """
    with pytest.raises(Exception):  # noqa: B017 - a mesma exceção da CL.9A
        validate_domain_matches_public_base_url("-not-valid-", "https://foo.duckdns.org")


def test_empty_domain_is_rejected() -> None:
    with pytest.raises(Exception):  # noqa: B017
        validate_domain_matches_public_base_url("", "https://foo.duckdns.org")


def test_empty_base_url_is_rejected() -> None:
    with pytest.raises(Exception):  # noqa: B017
        validate_domain_matches_public_base_url("foo.duckdns.org", "")


def test_check_domain_matches_wraps_success_as_ok() -> None:
    result = check_domain_matches_public_base_url("foo.duckdns.org", "https://foo.duckdns.org")
    assert result.status is CheckStatus.OK


def test_check_domain_matches_wraps_failure_as_failed_never_raises() -> None:
    result = check_domain_matches_public_base_url("foo.duckdns.org", "https://bar.duckdns.org")
    assert result.status is CheckStatus.FAILED


# -- Caddyfile contract (rendered file, not just the template) -----------------


def test_rendered_caddyfile_passes_the_contract_check(tmp_path: Path) -> None:
    path = _render("foo.duckdns.org", tmp_path)
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.OK


def test_unrendered_template_fails_the_contract_check() -> None:
    result = check_caddyfile_contract(TEMPLATE_PATH)
    assert result.status is CheckStatus.FAILED
    assert "placeholder" in result.detail


def test_missing_caddyfile_fails_the_contract_check(tmp_path: Path) -> None:
    result = check_caddyfile_contract(tmp_path / "absent.Caddyfile")
    assert result.status is CheckStatus.FAILED


def test_caddyfile_with_a_second_reverse_proxy_fails_the_contract_check(tmp_path: Path) -> None:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), "foo.duckdns.org")
    tampered = rendered.replace("respond 404", "reverse_proxy 127.0.0.1:9999\n\trespond 404")
    path = tmp_path / "tampered.Caddyfile"
    path.write_text(tampered, encoding="utf-8")
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.FAILED
    assert "reverse_proxy" in result.detail


def test_caddyfile_exposing_0_0_0_0_fails_the_contract_check(tmp_path: Path) -> None:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), "foo.duckdns.org")
    tampered = rendered.replace(
        "reverse_proxy @report_link 127.0.0.1:8080",
        "reverse_proxy @report_link 0.0.0.0:8080",
    )
    path = tmp_path / "tampered.Caddyfile"
    path.write_text(tampered, encoding="utf-8")
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.FAILED


def test_caddyfile_routing_healthz_fails_the_contract_check(tmp_path: Path) -> None:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), "foo.duckdns.org")
    tampered = rendered.replace(
        "respond 404", "reverse_proxy /healthz 127.0.0.1:8080\n\trespond 404"
    )
    path = tmp_path / "tampered.Caddyfile"
    path.write_text(tampered, encoding="utf-8")
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.FAILED
    assert "healthz" in result.detail


def test_caddyfile_with_a_log_block_fails_the_contract_check(tmp_path: Path) -> None:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), "foo.duckdns.org")
    tampered = rendered.replace("respond 404", "log {\n\t\toutput stdout\n\t}\n\trespond 404")
    path = tmp_path / "tampered.Caddyfile"
    path.write_text(tampered, encoding="utf-8")
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.FAILED
    assert "log" in result.detail.lower()


def test_caddyfile_missing_deny_by_default_fails_the_contract_check(tmp_path: Path) -> None:
    rendered = render_caddyfile(TEMPLATE_PATH.read_text(encoding="utf-8"), "foo.duckdns.org")
    tampered = rendered.replace("\trespond 404\n", "")
    path = tmp_path / "tampered.Caddyfile"
    path.write_text(tampered, encoding="utf-8")
    result = check_caddyfile_contract(path)
    assert result.status is CheckStatus.FAILED
    assert "404" in result.detail


# -- source staging --------------------------------------------------------------


def test_source_staging_passes_on_the_real_repo() -> None:
    result = check_source_staging(REPO_ROOT)
    assert result.status is CheckStatus.OK


def test_source_staging_fails_on_an_incomplete_tree(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    result = check_source_staging(tmp_path)
    assert result.status is CheckStatus.FAILED
    for marker in SOURCE_STAGING_MARKERS[1:]:
        assert marker in result.detail


def test_source_staging_fails_on_an_empty_directory(tmp_path: Path) -> None:
    result = check_source_staging(tmp_path)
    assert result.status is CheckStatus.FAILED


# -- run_activation_readiness: agregação, nunca ação mutável --------------------


def test_full_activation_readiness_passes_with_a_coherent_setup(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = _write_env(tmp_path / ".env")
    caddyfile = _render("foo.duckdns.org", tmp_path)

    results = run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=caddyfile,
        base_url="https://foo.duckdns.org",
        report_server_port=0,
    )
    assert exit_code_for(results) == 0


def test_full_activation_readiness_fails_on_domain_mismatch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = _write_env(tmp_path / ".env", REPORT_PUBLIC_BASE_URL="https://bar.duckdns.org")
    caddyfile = _render("foo.duckdns.org", tmp_path)

    results = run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=caddyfile,
        base_url="https://bar.duckdns.org",
        report_server_port=0,
    )
    assert exit_code_for(results) == 1
    names = {r.name for r in results if r.status is CheckStatus.FAILED}
    assert "domain_base_url_coherence" in names


def test_full_activation_readiness_fails_without_a_public_base_url(tmp_path: Path) -> None:
    """Reusa o gate de produção da CL.8 (`require_public_base_url=True`) —
    ativação nunca é menos exigente que o start de produção que ela prepara.
    """
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = _write_env(tmp_path / ".env", REPORT_PUBLIC_BASE_URL="")
    caddyfile = _render("foo.duckdns.org", tmp_path)

    results = run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=caddyfile,
        base_url="",
        report_server_port=0,
    )
    assert exit_code_for(results) == 1


def test_full_activation_readiness_fails_on_an_unrendered_caddyfile(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = _write_env(tmp_path / ".env")

    results = run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=TEMPLATE_PATH,
        base_url="https://foo.duckdns.org",
        report_server_port=0,
    )
    assert exit_code_for(results) == 1


def test_activation_readiness_never_writes_outside_the_given_paths(tmp_path: Path) -> None:
    """A garantia central de "nunca muta nada": roda duas vezes sobre o
    MESMO diretório e prova que nada novo aparece nele.
    """
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = _write_env(tmp_path / ".env")
    caddyfile = _render("foo.duckdns.org", tmp_path)

    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=caddyfile,
        base_url="https://foo.duckdns.org",
        report_server_port=0,
    )
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after


def test_activation_readiness_output_never_contains_a_credential_value(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    secret = "another-secret-value-77aa"
    env_file = _write_env(tmp_path / ".env", DISCORD_TOKEN=secret)
    caddyfile = _render("foo.duckdns.org", tmp_path)

    results = run_activation_readiness(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
        domain="foo.duckdns.org",
        caddyfile_path=caddyfile,
        base_url="https://foo.duckdns.org",
        report_server_port=0,
    )
    blob = " ".join(f"{r.name} {r.detail}" for r in results)
    assert secret not in blob


# -- deploy/activation-readiness.sh (static guards, never executed) -------------


def _script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _script_code() -> str:
    return "\n".join(
        line
        for line in _script_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def test_activation_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_activation_script_has_a_shebang_and_fails_fast() -> None:
    text = _script_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text


def test_activation_script_delegates_to_the_tested_python_command() -> None:
    assert "deploy-activation-readiness" in _script_code()


def test_activation_script_requires_domain_and_caddyfile() -> None:
    code = _script_code()
    assert '-z "${DOMAIN}"' in code
    assert '-z "${CADDYFILE}"' in code


def test_activation_script_never_calls_systemctl() -> None:
    """Puramente offline/validação — nunca inicia, reinicia ou recarrega
    nada.
    """
    assert "systemctl" not in _script_code()


def test_activation_script_never_makes_a_network_call() -> None:
    code = _script_code()
    for forbidden in ("curl", "wget", "dig ", "nslookup", "nc ", "ssh "):
        assert forbidden not in code


def test_activation_script_never_touches_a_firewall() -> None:
    text = _script_text().lower()
    for forbidden in ("ufw ", "iptables", "firewall-cmd"):
        assert forbidden not in text


def test_activation_script_never_opens_a_public_port() -> None:
    text = _script_text()
    assert "0.0.0.0" not in text
    for forbidden in ("--dport 8080", "allow 8080"):
        assert forbidden not in text


def test_activation_script_never_uses_a_windows_path() -> None:
    text = _script_text()
    assert "\\Scripts\\" not in text
    assert "C:\\" not in text


def test_activation_script_contains_no_secret_literal() -> None:
    text = _script_text()
    for marker in ("DISCORD_TOKEN=", "WCL_CLIENT_SECRET=", "BLIZZARD_CLIENT_SECRET="):
        assert marker not in text


# -- docs/activation-runbook.md ---------------------------------------------


def _runbook_text() -> str:
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def test_runbook_exists() -> None:
    assert RUNBOOK_PATH.is_file()


def _fenced_code_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:bash)?\n(.*?)```", text, re.DOTALL)


def test_runbook_never_recommends_curl_k() -> None:
    """O runbook CITA `curl -k`/`curl --insecure` de propósito, na tabela
    do que é proibido (prosa/markdown table, fora de um code fence) — mas
    nenhum bloco de comando REAL (` ```bash ` fence) pode conter isso.
    """
    text = _runbook_text()
    assert "curl -k" in text or "curl --insecure" in text  # a proibição está documentada
    for block in _fenced_code_blocks(text):
        assert "-k" not in block.split()
        assert "--insecure" not in block
        assert "verify=False" not in block


def test_runbook_never_recommends_disabling_ssh_host_key_checking() -> None:
    """Mesma lógica: a string pode aparecer na tabela de proibições
    (citando o que nunca fazer), nunca como um comando SSH de exemplo que
    o operador copiaria e colaria.
    """
    text = _runbook_text()
    assert "ssh -o StrictHostKeyChecking=no" not in text
    assert "ssh -o UserKnownHostsFile=/dev/null" not in text


def test_runbook_keeps_healthz_private() -> None:
    assert re.search(r"healthz[^\n]{0,60}404|404[^\n]{0,60}healthz", _runbook_text(), re.IGNORECASE)


def test_runbook_keeps_8080_private() -> None:
    text = _runbook_text()
    assert re.search(r"8080[^\n]{0,80}(refused|not.{0,20}public|never public)", text, re.IGNORECASE)


def test_runbook_never_hardcodes_a_fictional_real_domain_as_default() -> None:
    """Nenhum IPv4 além dos dois endereços estruturais já aprovados em CL.3/
    CL.9A (`127.0.0.1`, o loopback do ReportServer, e `0.0.0.0`, citado só
    como o que NUNCA usar) pode aparecer — um IP "de exemplo" fora desses
    dois seria facilmente confundido com o IP público real da VM, que
    ninguém deve inventar.
    """
    text = _runbook_text()
    allowed = {"127.0.0.1", "0.0.0.0"}
    found = set(re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text))
    assert found <= allowed, found - allowed


def test_runbook_documents_all_23_sequence_steps() -> None:
    text = _runbook_text()
    assert text.count("[MUTABLE]") >= 8
    assert text.count("[VALIDATE]") >= 8


def test_runbook_marks_real_discord_and_capability_tests_as_authorization_gated() -> None:
    text = _runbook_text().lower()
    assert "explicit authorization" in text or "explicitly authorized" in text


def test_runbook_documents_the_domain_mismatch_example() -> None:
    text = _runbook_text()
    assert "botgitgud.duckdns.org" in text
    assert "another-name.duckdns.org" in text


def test_runbook_documents_rollback_never_deletes_data() -> None:
    text = _runbook_text().lower()
    assert "rollback" in text
    assert "never delete" in text or "never revoke" in text


def test_runbook_documents_out_of_scope() -> None:
    text = _runbook_text()
    assert "FORA DE ESCOPO" in text


def test_runbook_references_the_activation_readiness_command() -> None:
    text = _runbook_text()
    assert "deploy-activation-readiness" in text
    assert "activation-readiness.sh" in text


# -- CL.8/CL.9A validations continue to hold ------------------------------------


def test_cl9a_domain_validation_still_used_unmodified() -> None:
    from botgitgud.ops.caddy_config import validate_caddy_domain

    assert validate_caddy_domain("foo.duckdns.org") == "foo.duckdns.org"
    with pytest.raises(Exception):  # noqa: B017
        validate_caddy_domain("")


def test_cl8_production_gate_still_governs_the_env_file(tmp_path: Path) -> None:
    from botgitgud.ops.deploy import validate_env_values

    ok = validate_env_values(dict(_VALID_ENV, REPORT_PUBLIC_BASE_URL=""))
    assert ok.ok  # host-preparation: warning only

    strict = validate_env_values(
        dict(_VALID_ENV, REPORT_PUBLIC_BASE_URL=""), require_public_base_url=True
    )
    assert not strict.ok
