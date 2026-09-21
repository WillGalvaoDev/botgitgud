"""CL.8 — preparação offline do deploy Linux.

Tudo aqui roda sem root, sem systemd, sem rede e sem tocar em nenhum
caminho real do host: os testes de backup/restore usam `tmp_path`, os de
`.env` usam dicts/arquivos temporários, e os scripts shell são verificados
estaticamente (nunca executados — vários exigiriam root e mutariam a
máquina).

Nenhum segredo real aparece: os valores usados são literais óbvios de
teste, e vários testes provam justamente que nenhum valor chega à saída.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from botgitgud.ops.deploy import (
    BACKUP_ENTRIES,
    PERSISTENT_DIRNAMES,
    BackupError,
    create_backup,
    list_backups,
    member_rejection_reason,
    parse_env_file,
    persistent_dirs,
    restore_backup,
    validate_env_file,
    validate_env_values,
)
from botgitgud.ops.preflight import (
    CheckStatus,
    check_persistent_dirs,
    check_systemd_unit,
    check_writable,
    exit_code_for,
    run_preflight,
    worst_status,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"

_VALID_ENV = {
    "DISCORD_TOKEN": "test-discord-value",
    "WCL_CLIENT_ID": "test-wcl-id",
    "WCL_CLIENT_SECRET": "test-wcl-secret",
    "BLIZZARD_CLIENT_ID": "test-bnet-id",
    "BLIZZARD_CLIENT_SECRET": "test-bnet-secret",
    "DATA_DIR": "/opt/botgitgud/data",
    "REPORT_SERVER_HOST": "127.0.0.1",
    "REPORT_SERVER_PORT": "8080",
    "REPORT_PUBLIC_BASE_URL": "https://example.com",
}


def _env(**overrides: str) -> dict[str, str]:
    values = dict(_VALID_ENV)
    values.update(overrides)
    return values


def _populate_data_dir(root: Path) -> None:
    for name in PERSISTENT_DIRNAMES:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "warehouse.duckdb").write_text("fake-warehouse", encoding="utf-8")
    (root / "raw" / "fight.parquet").write_text("raw-bytes", encoding="utf-8")
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "abc123.html").write_text("<html>relatorio</html>", encoding="utf-8")
    (root / "logs" / "botgitgud.jsonl").write_text('{"event":"x"}\n', encoding="utf-8")
    (root / "ops" / "analysis-runs" / "run.json").write_text("{}", encoding="utf-8")
    # Efêmeros — nunca devem entrar no backup.
    (root / "control" / "stop.request").write_text("", encoding="utf-8")
    (root / "control" / "bot.pid").write_text("4242", encoding="utf-8")
    (root / "ops-snapshot.json").write_text("{}", encoding="utf-8")
    (root / "spells.json").write_text("{}", encoding="utf-8")


# -- layout persistente --------------------------------------------------------


def test_persistent_dirs_cover_every_runtime_directory() -> None:
    """Espelha o que o código já resolve em runtime: se alguém adicionar um
    diretório novo sob data/ sem incluí-lo aqui, o bootstrap não o cria.
    """
    paths = persistent_dirs(Path("/opt/botgitgud/data"))
    names = {p.name for p in paths}
    assert {"raw", "logs", "control", "analysis-runs"} <= names
    assert "reports" not in names


def test_persistent_dirs_are_all_under_the_given_data_dir(tmp_path: Path) -> None:
    for path in persistent_dirs(tmp_path):
        assert path.is_relative_to(tmp_path)


# -- validação de .env ---------------------------------------------------------


def test_valid_production_env_passes() -> None:
    report = validate_env_values(_env())
    assert report.ok
    assert report.errors == ()


def test_each_missing_credential_is_reported_as_an_error() -> None:
    for name in (
        "DISCORD_TOKEN",
        "WCL_CLIENT_ID",
        "WCL_CLIENT_SECRET",
        "BLIZZARD_CLIENT_ID",
        "BLIZZARD_CLIENT_SECRET",
    ):
        report = validate_env_values(_env(**{name: ""}))
        assert not report.ok
        assert any(issue.variable == name for issue in report.errors)


def test_placeholder_credential_is_rejected() -> None:
    """Um template copiado não pode ser confundido com ambiente configurado."""
    report = validate_env_values(_env(DISCORD_TOKEN="changeme"))
    assert not report.ok
    assert any(issue.variable == "DISCORD_TOKEN" for issue in report.errors)


def test_retired_report_settings_are_ignored_in_every_env() -> None:
    report = validate_env_values(
        _env(
            REPORT_SERVER_HOST="0.0.0.0",
            REPORT_SERVER_PORT="invalid",
            REPORT_PUBLIC_BASE_URL="javascript:alert(1)",
        )
    )
    assert report.ok
    assert not any(issue.variable.startswith("REPORT_") for issue in report.issues)


def test_relative_data_dir_is_a_warning() -> None:
    report = validate_env_values(_env(DATA_DIR="data"))
    assert report.ok
    assert any(issue.variable == "DATA_DIR" for issue in report.warnings)


def test_posix_absolute_data_dir_is_accepted_even_when_validating_on_windows() -> None:
    """`Path("/opt/x").is_absolute()` é False no Windows. O valor descreve um
    caminho no host Linux de destino, então a validação usa semântica POSIX —
    sem isto, o mesmo .env passaria na VM e alertaria na máquina de
    desenvolvimento (onde a suíte roda).
    """
    report = validate_env_values(_env(DATA_DIR="/opt/botgitgud/data"))
    assert not any(issue.variable == "DATA_DIR" for issue in report.warnings)


def test_env_issues_never_carry_the_variable_value() -> None:
    """A garantia central: o diagnóstico é seguro para imprimir/logar."""
    secret = "super-secret-token-value-9f2b"
    report = validate_env_values(_env(DISCORD_TOKEN=""))
    blob = " ".join(f"{i.variable} {i.message}" for i in report.issues)
    assert secret not in blob
    for value in _VALID_ENV.values():
        if value not in ("127.0.0.1", "8080"):
            assert value not in blob


def test_parse_env_file_ignores_comments_and_strips_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comentario\n\nDISCORD_TOKEN="quoted-value"\nWCL_CLIENT_ID=plain\nBROKEN_LINE\n',
        encoding="utf-8",
    )
    values = parse_env_file(env_file)
    assert values["DISCORD_TOKEN"] == "quoted-value"
    assert values["WCL_CLIENT_ID"] == "plain"
    assert "BROKEN_LINE" not in values


def test_missing_env_file_is_an_error(tmp_path: Path) -> None:
    report = validate_env_file(tmp_path / "absent.env")
    assert not report.ok


def test_shipped_env_example_template_is_rejected_until_filled(tmp_path: Path) -> None:
    """O próprio template não pode passar na validação — é o teste que prova
    que copiar `deploy/env.example` e esquecer de editar não sobe o serviço.
    """
    report = validate_env_file(DEPLOY_DIR / "env.example")
    assert not report.ok


def test_shipped_env_example_has_no_retired_report_settings() -> None:
    values = parse_env_file(DEPLOY_DIR / "env.example")
    assert not any(name.startswith("REPORT_") for name in values)


def test_shipped_env_example_contains_no_real_secret() -> None:
    values = parse_env_file(DEPLOY_DIR / "env.example")
    for name in (
        "DISCORD_TOKEN",
        "WCL_CLIENT_ID",
        "WCL_CLIENT_SECRET",
        "BLIZZARD_CLIENT_ID",
        "BLIZZARD_CLIENT_SECRET",
    ):
        assert values[name] == ""


# -- backup --------------------------------------------------------------------


def test_backup_includes_every_durable_entry(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    result = create_backup(data_dir, tmp_path / "backups")
    assert set(result.included) == set(BACKUP_ENTRIES)
    assert result.archive.is_file()


def test_backup_excludes_control_directory_entirely(tmp_path: Path) -> None:
    """`stop.request` restaurado mandaria o bot desligar; `bot.pid` e
    `supervisor.lock` confundiriam a supervisão no boot seguinte.
    """
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    result = create_backup(data_dir, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        names = tar.getnames()
    assert not any("control" in name for name in names)
    assert not any("stop.request" in name for name in names)
    assert not any("bot.pid" in name for name in names)


def test_backup_excludes_regenerable_runtime_caches(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    result = create_backup(data_dir, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        names = tar.getnames()
    assert not any(name.endswith("ops-snapshot.json") for name in names)
    assert not any(name.endswith("spells.json") for name in names)


def test_backup_never_includes_source_venv_or_env(tmp_path: Path) -> None:
    """A allowlist é o que estrutura essa garantia: mesmo com `.env` e uma
    venv plantados dentro de data/, nada fora de BACKUP_ENTRIES entra.
    """
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    (data_dir / ".env").write_text("DISCORD_TOKEN=leaked", encoding="utf-8")
    (data_dir / ".venv").mkdir()
    (data_dir / ".venv" / "pyvenv.cfg").write_text("x", encoding="utf-8")
    (data_dir / "src.py").write_text("print('code')", encoding="utf-8")

    result = create_backup(data_dir, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        names = tar.getnames()
        payload = b"".join(
            member.read() for name in names if (member := tar.extractfile(name)) is not None
        )
    assert not any(".env" in name for name in names)
    assert not any(".venv" in name for name in names)
    assert not any(name.endswith("src.py") for name in names)
    assert b"leaked" not in payload


def test_backup_reports_missing_entries_as_skipped(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "reports").mkdir()
    result = create_backup(data_dir, tmp_path / "backups")
    assert "reports" in result.included
    assert "warehouse.duckdb" in result.skipped


def test_backup_entries_are_stored_under_a_single_data_prefix(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    result = create_backup(data_dir, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        names = tar.getnames()
    assert all(name == "data" or name.startswith("data/") for name in names)


def test_backup_normalizes_ownership_metadata(tmp_path: Path) -> None:
    """UID/GID do host de origem não devem viajar no arquivo — o restore roda
    como o usuário do serviço, que pode ter outro id.
    """
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    result = create_backup(data_dir, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        for member in tar.getmembers():
            assert member.uid == 0
            assert member.uname == ""


def test_backup_on_missing_data_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        create_backup(tmp_path / "absent", tmp_path / "backups")


def test_list_backups_returns_newest_first(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    dest.mkdir()
    for stamp in ("20260101-000000", "20260301-000000", "20260201-000000"):
        (dest / f"botgitgud-backup-{stamp}.tar.gz").write_text("x", encoding="utf-8")
    (dest / "unrelated.txt").write_text("x", encoding="utf-8")
    found = [p.name for p in list_backups(dest)]
    assert found == [
        "botgitgud-backup-20260301-000000.tar.gz",
        "botgitgud-backup-20260201-000000.tar.gz",
        "botgitgud-backup-20260101-000000.tar.gz",
    ]


# -- restore -------------------------------------------------------------------


def test_backup_restore_round_trip_preserves_content(tmp_path: Path) -> None:
    source = tmp_path / "data"
    _populate_data_dir(source)
    result = create_backup(source, tmp_path / "backups")

    target = tmp_path / "restored"
    restore_backup(result.archive, target)

    assert (target / "warehouse.duckdb").read_text(encoding="utf-8") == "fake-warehouse"
    assert (target / "reports" / "abc123.html").read_text(encoding="utf-8") == (
        "<html>relatorio</html>"
    )
    assert (target / "raw" / "fight.parquet").read_text(encoding="utf-8") == "raw-bytes"
    assert (target / "ops" / "analysis-runs" / "run.json").is_file()


def test_restore_never_recreates_the_control_directory(tmp_path: Path) -> None:
    source = tmp_path / "data"
    _populate_data_dir(source)
    result = create_backup(source, tmp_path / "backups")
    target = tmp_path / "restored"
    restore_backup(result.archive, target)
    assert not (target / "control").exists()


def test_restore_refuses_to_overwrite_by_default(tmp_path: Path) -> None:
    source = tmp_path / "data"
    _populate_data_dir(source)
    result = create_backup(source, tmp_path / "backups")

    target = tmp_path / "restored"
    restore_backup(result.archive, target)
    with pytest.raises(BackupError, match="overwrite"):
        restore_backup(result.archive, target)


def test_restore_with_overwrite_succeeds(tmp_path: Path) -> None:
    source = tmp_path / "data"
    _populate_data_dir(source)
    result = create_backup(source, tmp_path / "backups")

    target = tmp_path / "restored"
    restore_backup(result.archive, target)
    (target / "warehouse.duckdb").write_text("clobbered", encoding="utf-8")
    restore_backup(result.archive, target, overwrite=True)
    assert (target / "warehouse.duckdb").read_text(encoding="utf-8") == "fake-warehouse"


def test_restore_rejects_a_path_traversal_member(tmp_path: Path) -> None:
    """Defesa explícita contra CVE-2007-4559: um arquivo malicioso nunca
    escreve fora do data_dir de destino.
    """
    malicious = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("owned", encoding="utf-8")
    with tarfile.open(malicious, "w:gz") as tar:
        tar.add(payload, arcname="../../escaped.txt")

    with pytest.raises(BackupError, match="inseguro"):
        restore_backup(malicious, tmp_path / "restored")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_restore_rejects_members_outside_the_data_prefix(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x", encoding="utf-8")
    with tarfile.open(foreign, "w:gz") as tar:
        tar.add(payload, arcname="etc/passwd")
    with pytest.raises(BackupError, match="inseguro"):
        restore_backup(foreign, tmp_path / "restored")


def test_restore_of_missing_archive_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        restore_backup(tmp_path / "absent.tar.gz", tmp_path / "restored")


# -- restore: testes adversariais (gate 4) -------------------------------------
#
# O arquivo de backup é um artefato que circula e pode ser substituído. Que ele
# NORMALMENTE seja produzido por este próprio programa não é garantia de nada —
# é justamente o modelo de ameaça de um restore. Cada caso abaixo constrói um
# tar hostil à mão e exige recusa ANTES de qualquer extração.


def _hostile_archive(tmp_path: Path, name: str, arcname: str) -> Path:
    archive = tmp_path / name
    payload = tmp_path / "payload.txt"
    payload.write_text("owned", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname=arcname)
    return archive


@pytest.mark.parametrize(
    ("label", "arcname"),
    [
        ("absoluto_posix", "/etc/cron.d/evil"),
        ("absoluto_windows", "C:/Windows/System32/evil"),
        ("traversal_simples", "../escaped.txt"),
        ("traversal_sob_prefixo", "data/../../escaped.txt"),
        ("traversal_profundo", "data/raw/../../../escaped.txt"),
        ("fora_do_prefixo", "etc/passwd"),
        ("fora_da_allowlist", "data/segredos/evil.txt"),
        ("barra_invertida", "data\\..\\..\\escaped.txt"),
        ("barra_invertida_sob_prefixo", "data/..\\..\\escaped.txt"),
    ],
)
def test_restore_rejects_hostile_member_names(tmp_path: Path, label: str, arcname: str) -> None:
    archive = _hostile_archive(tmp_path, f"{label}.tar.gz", arcname)
    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="inseguro"):
        restore_backup(archive, target)
    # Nada foi escrito: nem no destino, nem fora dele.
    assert not target.exists() or list(target.iterdir()) == []
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_restore_rejects_a_symlink_member(tmp_path: Path) -> None:
    """Symlink é o vetor clássico de escape: `data/raw/x -> /etc/passwd`
    faria uma escrita posterior atravessar o link.
    """
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("data/raw/escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(BackupError, match="symlink"):
        restore_backup(archive, tmp_path / "restored")


def test_restore_rejects_a_hardlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "hardlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("data/raw/escape")
        info.type = tarfile.LNKTYPE
        info.linkname = "../../../etc/passwd"
        tar.addfile(info)
    with pytest.raises(BackupError, match="hardlink"):
        restore_backup(archive, tmp_path / "restored")


@pytest.mark.parametrize("member_type", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_restore_rejects_special_file_members(tmp_path: Path, member_type: bytes) -> None:
    archive = tmp_path / "special.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("data/raw/special")
        info.type = member_type
        tar.addfile(info)
    with pytest.raises(BackupError, match="inseguro"):
        restore_backup(archive, tmp_path / "restored")


def test_restore_rejects_the_whole_archive_when_one_member_is_hostile(tmp_path: Path) -> None:
    """Validação COMPLETA antes de extrair: um membro ruim invalida o
    arquivo inteiro, sem deixar metade do conteúdo no disco.
    """
    source = tmp_path / "data"
    _populate_data_dir(source)
    good = create_backup(source, tmp_path / "backups").archive

    poisoned = tmp_path / "poisoned.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("owned", encoding="utf-8")
    with tarfile.open(poisoned, "w:gz") as out, tarfile.open(good, "r:gz") as src:
        for member in src.getmembers():
            extracted = src.extractfile(member)
            out.addfile(member, extracted) if extracted else out.addfile(member)
        out.add(payload, arcname="data/../../escaped.txt")

    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="inseguro"):
        restore_backup(poisoned, target)
    assert not target.exists() or list(target.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("/etc/cron.d/evil", "absoluto"),
        ("/data/raw/x", "absoluto"),
        ("C:/Windows/evil", "unidade"),
        ("C:\\Windows\\evil", "barra invertida"),
        ("data\\..\\..\\escaped.txt", "barra invertida"),
        ("data/raw\\..\\..\\escaped.txt", "barra invertida"),
        ("", "vazio"),
        (".", "vazio"),
    ],
)
def test_member_rejection_reason_catches_raw_hostile_names(name: str, expected: str) -> None:
    """Testado no nível da função, não via `tarfile.add(arcname=...)`.

    `tarfile` NORMALIZA o nome ao gravar — remove a `/` inicial e converte
    barras invertidas — então um arquivo construído com a própria stdlib
    nunca chega a exercitar estes ramos. Um tar hostil produzido por outra
    ferramenta chega, e é contra ele que esta defesa existe.
    """
    reason = member_rejection_reason(name, tarfile.REGTYPE)
    assert reason is not None, name
    assert expected in reason, f"{name}: {reason}"


def test_member_rejection_reason_accepts_legitimate_members() -> None:
    """A contraparte positiva: o que o próprio backup escreve tem de passar,
    senão a guarda estaria só quebrando o caminho feliz.
    """
    for name in (
        "data",
        "data/warehouse.duckdb",
        "data/raw",
        "data/raw/fight.parquet",
        "data/ops/analysis-runs/run.json",
        "data/logs/botgitgud.jsonl",
    ):
        assert member_rejection_reason(name, tarfile.REGTYPE) is None, name
    assert member_rejection_reason("data/raw", tarfile.DIRTYPE) is None


def test_every_member_the_backup_writes_passes_the_restore_guard(tmp_path: Path) -> None:
    """Propriedade de ida e volta: nenhum arquivo gerado por `create_backup`
    pode ser recusado pela validação do restore.
    """
    source = tmp_path / "data"
    _populate_data_dir(source)
    result = create_backup(source, tmp_path / "backups")
    with tarfile.open(result.archive, "r:gz") as tar:
        for member in tar.getmembers():
            assert member_rejection_reason(member.name, member.type) is None, member.name


# -- preflight -----------------------------------------------------------------


def test_preflight_reports_missing_persistent_dirs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    results = check_persistent_dirs(data_dir)
    assert any(r.status is CheckStatus.FAILED for r in results)


def test_preflight_accepts_a_fully_prepared_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    results = check_persistent_dirs(data_dir)
    assert all(r.status is CheckStatus.OK for r in results)


def test_preflight_detects_a_missing_data_dir(tmp_path: Path) -> None:
    results = check_persistent_dirs(tmp_path / "absent")
    assert results[0].status is CheckStatus.FAILED


def test_writable_check_passes_on_a_writable_dir_and_leaves_no_probe(tmp_path: Path) -> None:
    result = check_writable(tmp_path)
    assert result.status is CheckStatus.OK
    assert list(tmp_path.iterdir()) == []


def test_systemd_unit_check_finds_the_versioned_unit() -> None:
    assert check_systemd_unit(REPO_ROOT).status is CheckStatus.OK


def test_systemd_unit_check_fails_when_absent(tmp_path: Path) -> None:
    assert check_systemd_unit(tmp_path).status is CheckStatus.FAILED


def test_exit_code_is_nonzero_only_on_failure() -> None:
    from botgitgud.ops.preflight import CheckResult

    ok = [CheckResult("a", CheckStatus.OK, "")]
    warned = [CheckResult("a", CheckStatus.WARNING, "")]
    failed = [CheckResult("a", CheckStatus.FAILED, "")]
    assert exit_code_for(ok) == 0
    assert exit_code_for(warned) == 0
    assert exit_code_for(failed) == 1
    assert worst_status(warned + failed) is CheckStatus.FAILED


def test_full_preflight_run_fails_when_env_is_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    results = run_preflight(
        data_dir=data_dir,
        env_path=tmp_path / "absent.env",
        repo_root=REPO_ROOT,
    )
    assert exit_code_for(results) == 1


def test_full_preflight_run_passes_with_a_valid_setup(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in _VALID_ENV.items()),
        encoding="utf-8",
    )
    results = run_preflight(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
    )
    assert exit_code_for(results) == 0


def test_preflight_ignores_retired_report_settings(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in _env(REPORT_PUBLIC_BASE_URL="").items()),
        encoding="utf-8",
    )
    results = run_preflight(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
    )
    assert exit_code_for(results) == 0


def test_preflight_output_never_contains_a_credential_value(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_data_dir(data_dir)
    env_file = tmp_path / ".env"
    secret = "another-secret-value-77aa"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in _env(DISCORD_TOKEN=secret).items()),
        encoding="utf-8",
    )
    results = run_preflight(
        data_dir=data_dir,
        env_path=env_file,
        repo_root=REPO_ROOT,
    )
    blob = " ".join(f"{r.name} {r.detail}" for r in results)
    assert secret not in blob
