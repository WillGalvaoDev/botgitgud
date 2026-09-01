"""GH.0 — guard de segurança de publicação.

Tudo offline: a política é pura (recebe uma lista de caminhos), então
nenhum teste aqui precisa de um repositório git de verdade, de rede, ou
de tocar no `data/` real desta máquina. Os casos "proibido" são caminhos
sintéticos; os casos "permitido" usam os caminhos reais que este
repositório de fato versiona, para provar que o guard não reprova o
próprio projeto.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from botgitgud.ops.preflight import CheckStatus, exit_code_for
from botgitgud.ops.publication import (
    FAIL_FILE_BYTES,
    SELF_EXCLUDED_PATHS,
    WARN_FILE_BYTES,
    check_file_sizes,
    check_forbidden_paths,
    check_secret_markers,
    forbidden_reason,
    run_publication_safety,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# -- caminhos proibidos ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        ".env.local",
        "deploy/.env",
        "certs/server.pem",
        "certs/server.key",
        "id_rsa",
        "home/id_ed25519",
        "credentials.json",
        "secrets.yaml",
        "data/warehouse.duckdb",
        "data/raw/fight.parquet",
        "data/reports/abc.html",
        "data/logs/botgitgud.jsonl",
        "data/control/stop.request",
        "warehouse.duckdb",
        "backups/botgitgud-backup-20260101-000000.tar.gz",
        "anything.tar.gz",
        "state.sqlite3",
        "keys/client.p12",
    ],
)
def test_forbidden_paths_are_rejected(path: str) -> None:
    assert forbidden_reason(path) is not None, path


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "deploy/env.example",
        "README.md",
        "pyproject.toml",
        "spells.json",
        "src/botgitgud/cli.py",
        "tests/fixtures/cassettes/0014b69d2fc243b5.json",
        "tests/golden/test_legacy_output.py",
        "legacy/bot.py",
        "deploy/caddy/Caddyfile.template",
        "deploy/systemd/botgitgud.service",
        "docs/runbook.md",
        "scripts/bot-supervisor.ps1",
    ],
)
def test_legitimate_tracked_paths_are_allowed(path: str) -> None:
    """Contraparte positiva: sem isto o guard poderia reprovar tudo e os
    testes acima continuariam passando.
    """
    assert forbidden_reason(path) is None, path


def test_env_example_is_never_confused_with_a_real_env() -> None:
    """O template versionado e o segredo real diferem por UM sufixo — é
    exatamente o tipo de regra que precisa de teste explícito.
    """
    assert forbidden_reason(".env.example") is None
    assert forbidden_reason(".env") is not None
    assert forbidden_reason(".env.exemplo") is not None  # variante ≠ .env.example


def test_windows_style_separators_are_normalized() -> None:
    assert forbidden_reason("data\\warehouse.duckdb") is not None


def test_check_forbidden_paths_reports_ok_on_a_clean_list() -> None:
    result = check_forbidden_paths(["README.md", "src/botgitgud/cli.py"])
    assert result.status is CheckStatus.OK


def test_check_forbidden_paths_fails_and_names_the_offender() -> None:
    result = check_forbidden_paths(["README.md", "data/warehouse.duckdb"])
    assert result.status is CheckStatus.FAILED
    assert "data/warehouse.duckdb" in result.detail


# -- tamanho de arquivo ----------------------------------------------------------


def test_small_files_pass(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("x", encoding="utf-8")
    results = check_file_sizes(tmp_path, ["small.txt"])
    assert all(r.status is CheckStatus.OK for r in results)


def test_file_above_warn_threshold_warns(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * (WARN_FILE_BYTES + 1))
    results = check_file_sizes(tmp_path, ["big.bin"])
    assert any(r.status is CheckStatus.WARNING for r in results)
    # Warning não reprova: as cassettes deste repositório caem nessa faixa.
    assert exit_code_for(results) == 0


def test_file_above_fail_threshold_fails(tmp_path: Path) -> None:
    (tmp_path / "huge.bin").write_bytes(b"x" * (FAIL_FILE_BYTES + 1))
    results = check_file_sizes(tmp_path, ["huge.bin"])
    assert any(r.status is CheckStatus.FAILED for r in results)
    assert exit_code_for(results) == 1


def test_missing_file_is_skipped_not_crashed(tmp_path: Path) -> None:
    results = check_file_sizes(tmp_path, ["nao-existe.bin"])
    assert all(r.status is CheckStatus.OK for r in results)


# -- marcadores de segredo --------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
    ],
)
def test_private_key_block_is_detected(tmp_path: Path, marker: str) -> None:
    (tmp_path / "leaked.txt").write_text(f"{marker}\nAAAA\n", encoding="utf-8")
    result = check_secret_markers(tmp_path, ["leaked.txt"])
    assert result.status is CheckStatus.FAILED


def test_webhook_url_is_detected(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(
        "https://discord.com/api/webhooks/123456789/abcdefg", encoding="utf-8"
    )
    result = check_secret_markers(tmp_path, ["notes.md"])
    assert result.status is CheckStatus.FAILED


def test_ordinary_text_is_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "ok.md").write_text(
        "Este projeto nunca versiona .env. Ver docs/runbook.md.", encoding="utf-8"
    )
    result = check_secret_markers(tmp_path, ["ok.md"])
    assert result.status is CheckStatus.OK


def test_binary_and_unknown_suffixes_are_skipped(tmp_path: Path) -> None:
    """Só arquivos de texto conhecidos são lidos — evita varrer 265 MB de
    cassettes a cada execução.
    """
    (tmp_path / "blob.bin").write_bytes(b"-----BEGIN RSA PRIVATE KEY-----")
    result = check_secret_markers(tmp_path, ["blob.bin"])
    assert result.status is CheckStatus.OK


def test_the_detector_does_not_flag_itself(tmp_path: Path) -> None:
    """Regressão de um defeito real encontrado antes do commit: o próprio
    `publication.py` e este arquivo de teste contêm os marcadores literais
    que o detector procura, então sem a exceção por caminho o
    `publication-check` reprovaria a si mesmo assim que fosse commitado —
    tornando o guard permanentemente vermelho e, portanto, inútil.
    """
    result = check_secret_markers(REPO_ROOT, sorted(SELF_EXCLUDED_PATHS))
    assert result.status is CheckStatus.OK


def test_the_self_exclusion_is_narrow(tmp_path: Path) -> None:
    """A exceção não pode virar um buraco: qualquer OUTRO arquivo com um
    bloco de chave continua sendo reprovado, inclusive um com nome
    parecido.
    """
    (tmp_path / "publication.py").write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    result = check_secret_markers(tmp_path, ["publication.py"])
    assert result.status is CheckStatus.FAILED, "só o caminho EXATO é excluído"


def test_self_excluded_paths_still_exist_in_the_repository() -> None:
    """Se um desses arquivos for renomeado, a exceção vira letra morta
    silenciosamente — e o detector volta a se auto-reprovar.
    """
    for rel in SELF_EXCLUDED_PATHS:
        assert (REPO_ROOT / rel).is_file(), rel


def test_secret_marker_result_states_it_is_not_a_substitute() -> None:
    """A honestidade sobre o limite da checagem é parte do contrato — se
    alguém remover esse aviso, o guard começa a parecer uma garantia que
    ele não é.
    """
    result = check_secret_markers(REPO_ROOT, ["README.md"])
    assert "substitui" in result.detail.lower()


# -- integração: o próprio repositório passa -------------------------------------


def test_this_repository_passes_the_publication_check() -> None:
    """O teste que mais importa: os arquivos que este projeto realmente
    versiona não podem disparar nenhuma FALHA.
    """
    tracked = [
        str(p.relative_to(REPO_ROOT)).replace("\\", "/")
        for p in [
            REPO_ROOT / "README.md",
            REPO_ROOT / "pyproject.toml",
            REPO_ROOT / ".env.example",
            REPO_ROOT / "spells.json",
            REPO_ROOT / "src" / "botgitgud" / "cli.py",
            REPO_ROOT / "legacy" / "bot.py",
        ]
    ]
    results = run_publication_safety(REPO_ROOT, tracked)
    assert exit_code_for(results) == 0


def test_run_publication_safety_fails_when_runtime_data_is_tracked() -> None:
    results = run_publication_safety(REPO_ROOT, ["README.md", "data/warehouse.duckdb"])
    assert exit_code_for(results) == 1
    assert any(r.name == "forbidden_paths" and r.status is CheckStatus.FAILED for r in results)


def test_run_publication_safety_fails_when_a_real_env_is_tracked() -> None:
    results = run_publication_safety(REPO_ROOT, ["README.md", ".env"])
    assert exit_code_for(results) == 1
