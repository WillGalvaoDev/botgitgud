"""CL.8 — guards estáticos dos scripts de deploy e da documentação.

Os scripts NÃO são executados: `bootstrap-linux.sh` e `install-systemd.sh`
exigem root e mutariam a máquina (useradd, chown -R, systemctl), e
`preflight.sh`/`backup.sh` assumem uma venv em `/opt/botgitgud`. O que se
verifica aqui é o contrato do texto — as propriedades que este ticket
exigiu e que uma edição futura poderia derrubar sem ninguém notar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
DOC_PATH = REPO_ROOT / "docs" / "linux-deployment.md"

SCRIPTS = (
    "bootstrap-linux.sh",
    "preflight.sh",
    "install-systemd.sh",
    "backup.sh",
    "restore.sh",
)

# Portas que este ticket proíbe explicitamente de abrir/expor.
FORBIDDEN_PORTS = ("8080", "8000", "3000", ":80", ":443")


def _read(name: str) -> str:
    return (DEPLOY_DIR / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """Só as linhas executáveis.

    Vários destes scripts documentam em comentário exatamente o que NÃO
    fazem ("nao instala Caddy", "sem S3", "use --start"). Procurar a
    palavra no arquivo inteiro confundiria essa documentação com uma
    violação — o que importa é o que o shell executa.
    """
    lines = []
    for raw in _read(name).splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(raw)
    return "\n".join(lines)


def _without_string_literals(text: str) -> str:
    """Remove o conteúdo entre aspas duplas, preservando as quebras de linha.

    Necessário porque as mensagens instrutivas destes scripts são strings de
    MÚLTIPLAS linhas (`die "...\\n  sudo git clone ...\\n"`): uma varredura
    linha a linha veria `sudo git clone` no início de uma linha e concluiria,
    errado, que o script clona o repositório.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "\n":
            out.append(char)
            continue
        if not in_string:
            out.append(char)
    return "".join(out)


def _executes(name: str, command: str) -> bool:
    """O script EXECUTA `command`?

    Distinto de "menciona": estes scripts instruem o operador dentro de
    mensagens (`die "... sudo git clone ..."`, `echo "sudo systemctl stop"`).
    Essas linhas são executáveis (é um `echo`), mas o comando citado não é
    executado — só conta uma linha que, fora de qualquer string, COMECE com
    o comando.
    """
    for raw in _without_string_literals(_code(name)).splitlines():
        stripped = raw.strip()
        for prefix in ("sudo ", ""):
            if stripped.startswith(f"{prefix}{command}"):
                return True
    return False


def test_executes_helper_is_not_vacuous() -> None:
    """Sem isto, um `_executes` que sempre devolvesse False faria TODOS os
    guards de "não executa X" passarem sem verificar nada.
    """
    # Executados de verdade:
    assert _executes("bootstrap-linux.sh", "chown")
    assert _executes("install-systemd.sh", "systemctl daemon-reload")
    assert _executes("install-systemd.sh", "systemctl enable")
    # Só citados dentro de mensagens:
    assert not _executes("bootstrap-linux.sh", "git clone")
    assert not _executes("backup.sh", "systemctl stop")


def test_string_literal_stripper_preserves_line_structure() -> None:
    stripped = _without_string_literals('echo "a\nb"\nchown x\n')
    assert "chown x" in stripped
    assert "a" not in stripped.split("chown")[0].replace("echo", "")


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_exists(name: str) -> None:
    assert (DEPLOY_DIR / name).is_file()


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_has_a_shebang(name: str) -> None:
    assert _read(name).startswith("#!/usr/bin/env bash")


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_fails_fast(name: str) -> None:
    """`set -euo pipefail`: um deploy que continua depois de um passo que
    falhou é pior do que um que para.
    """
    assert "set -euo pipefail" in _read(name)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_contains_no_secret_literal(name: str) -> None:
    text = _read(name)
    for marker in ("DISCORD_TOKEN=", "WCL_CLIENT_SECRET=", "BLIZZARD_CLIENT_SECRET="):
        assert marker not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_never_uses_a_windows_path(name: str) -> None:
    text = _read(name)
    assert "\\Scripts\\" not in text
    assert "C:\\" not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_does_not_touch_ssh_or_firewall(name: str) -> None:
    """Fora de escopo por instrução explícita do ticket."""
    text = _read(name).lower()
    for forbidden in ("sshd_config", "ufw ", "iptables", "firewall-cmd", "authorized_keys"):
        assert forbidden not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_does_not_install_or_configure_caddy_or_duckdns(name: str) -> None:
    """Só o código conta: os comentários mencionam Caddy/DuckDNS justamente
    para registrar que estão fora de escopo.
    """
    code = _code(name).lower()
    assert "caddy" not in code
    assert "duckdns" not in code


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_has_no_unbounded_destructive_delete(name: str) -> None:
    text = _read(name)
    for dangerous in ("rm -rf /", "rm -rf ${REPO_DIR}", 'rm -rf "${REPO_DIR}"', "mkfs", "dd if="):
        assert dangerous not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_never_opens_a_public_port(name: str) -> None:
    """Nenhum script pode publicar o report server nem abrir 80/443."""
    text = _read(name)
    for port in FORBIDDEN_PORTS:
        assert f"--dport {port}" not in text
        assert f"allow {port}" not in text
    assert "0.0.0.0" not in text


# -- bootstrap-linux.sh --------------------------------------------------------


def test_bootstrap_requires_root() -> None:
    assert "id -u" in _read("bootstrap-linux.sh")


def test_bootstrap_creates_user_and_group_idempotently() -> None:
    text = _read("bootstrap-linux.sh")
    # A criação sempre é guardada por uma consulta de existência.
    assert "getent group" in text
    assert 'id -u "${SERVICE_USER}"' in text
    assert "groupadd --system" in text
    assert "useradd --system" in text


def test_bootstrap_creates_the_service_user_without_a_home() -> None:
    """Pareado com ProtectHome=true na unit — ver deploy/systemd/."""
    assert "--no-create-home" in _read("bootstrap-linux.sh")


def test_bootstrap_creates_every_persistent_dir() -> None:
    from botgitgud.ops.deploy import PERSISTENT_DIRNAMES

    text = _read("bootstrap-linux.sh")
    for name in PERSISTENT_DIRNAMES:
        assert name in text, f"bootstrap não cria {name}"


def test_bootstrap_uses_mkdir_p_so_it_is_idempotent() -> None:
    assert "mkdir -p" in _read("bootstrap-linux.sh")


def test_bootstrap_installs_with_the_repo_mechanism() -> None:
    """`pip install -e .` — o mecanismo que o repositório já adota; não
    introduz uv, poetry nem requirements.txt nesta etapa.
    """
    text = _read("bootstrap-linux.sh")
    assert "pip" in text and "install -e" in text
    assert "uv " not in text
    assert "poetry" not in text


def test_bootstrap_never_writes_env_values() -> None:
    """Não inventa valor de .env: no máximo ajusta permissão e instrui."""
    text = _read("bootstrap-linux.sh")
    assert "REPORT_PUBLIC_BASE_URL=" not in text
    assert "chmod 600" in text


def test_bootstrap_does_not_start_the_service() -> None:
    text = _read("bootstrap-linux.sh")
    assert "systemctl start" not in text
    assert "systemctl enable" not in text


def test_bootstrap_does_not_run_apt_without_opt_in() -> None:
    """`apt-get install` só dentro do ramo --with-apt."""
    text = _read("bootstrap-linux.sh")
    assert "WITH_APT" in text
    before_optin = text.split('WITH_APT}" -eq 1')[0]
    assert "apt-get install" not in before_optin


# -- install-systemd.sh --------------------------------------------------------


def test_install_validates_the_unit_before_copying() -> None:
    text = _read("install-systemd.sh")
    verify_at = text.find("systemd-analyze verify")
    copy_at = text.find("install -m 0644")
    assert verify_at != -1 and copy_at != -1
    assert verify_at < copy_at


def test_install_refuses_a_unit_that_does_not_point_at_the_supervisor() -> None:
    assert "botgitgud.cli supervise" in _read("install-systemd.sh")


def test_install_runs_daemon_reload_and_enable() -> None:
    text = _read("install-systemd.sh")
    assert "systemctl daemon-reload" in text
    assert "systemctl enable" in text


def test_install_does_not_start_by_default() -> None:
    """O start é opt-in (`--start`) e ainda assim exige .env válido: a
    validação precisa vir ANTES do `systemctl start` executável — o
    `systemctl start` citado numa mensagem de log não conta.
    """
    lines = _code("install-systemd.sh").splitlines()
    assert any("DO_START" in line for line in lines)

    # A INVOCAÇÃO real é uma linha que começa com `systemctl start`; uma
    # mensagem de log que apenas cita o comando não é um start.
    start_line = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("systemctl start")),
        None,
    )
    validate_line = next(
        (i for i, line in enumerate(lines) if "deploy-validate-env" in line),
        None,
    )
    assert start_line is not None
    assert validate_line is not None
    assert validate_line < start_line


def test_install_is_idempotent_about_copying() -> None:
    assert "cmp -s" in _read("install-systemd.sh")


def test_install_start_uses_the_environment_gate() -> None:
    code = _code("install-systemd.sh")
    assert "deploy-validate-env" in code
    assert "--production" not in code


def test_install_has_no_public_report_start_gate() -> None:
    text = _read("install-systemd.sh")
    assert "REPORT_PUBLIC_BASE_URL" not in text
    assert "Caddy" not in text


def test_install_never_invents_a_public_base_url() -> None:
    """Nenhum script pode contornar o gate escrevendo uma URL temporária."""
    for name in SCRIPTS:
        code = _code(name)
        assert "REPORT_PUBLIC_BASE_URL=" not in code
        assert "duckdns.org" not in code


# -- bootstrap: contrato de staging do source tree (gate 3) --------------------


def test_bootstrap_does_not_fetch_code_from_the_network() -> None:
    """Gate 3: o operador põe a árvore em /opt/botgitgud; o script não baixa
    nada nesta etapa.
    """
    for network in ("git clone", "curl", "wget", "git pull", "git fetch", "pip download"):
        assert not _executes("bootstrap-linux.sh", network), network
    # `git clone` aparece apenas na mensagem que instrui o operador.
    assert "git clone" in _read("bootstrap-linux.sh")


def test_bootstrap_verifies_repository_marker_files() -> None:
    """Um ${REPO_DIR} parcial (rsync interrompido, checkout errado) precisa
    falhar aqui, com mensagem acionável, não no meio do pip install.
    """
    code = _code("bootstrap-linux.sh")
    for marker in (
        "pyproject.toml",
        "src/botgitgud/__init__.py",
        "src/botgitgud/cli.py",
        "deploy/systemd/botgitgud.service",
    ):
        assert marker in code, f"bootstrap não verifica o marcador {marker}"


def test_bootstrap_install_target_is_explicit_never_cwd_relative() -> None:
    """Gate 3: `pip install -e .` resolveria contra o cwd de quem chamou o
    script. O alvo tem de ser o caminho explícito.
    """
    code = _code("bootstrap-linux.sh")
    assert 'install -e "${REPO_DIR}"' in code
    assert "install -e ." not in code


def test_bootstrap_verifies_the_installed_package_resolves_to_repo_dir() -> None:
    """Uma venv com instalação anterior apontando para outra árvore é um modo
    de falha silencioso — o bootstrap prova o contrário antes de terminar.
    """
    code = _code("bootstrap-linux.sh")
    assert "INSTALLED_AT" in code
    assert "botgitgud.__file__" in code


# -- preflight.sh / backup.sh / restore.sh -------------------------------------


def test_preflight_delegates_to_the_tested_python_command() -> None:
    assert "deploy-preflight" in _read("preflight.sh")


def test_preflight_tolerates_a_host_without_systemd() -> None:
    """A suíte roda onde não há systemd; a ausência de systemd-analyze não
    pode reprovar o preflight.
    """
    text = _read("preflight.sh")
    assert "command -v systemd-analyze" in text


def test_preflight_propagates_a_nonzero_exit() -> None:
    text = _read("preflight.sh")
    assert "STATUS=1" in text
    assert 'exit "${STATUS}"' in text


def test_backup_delegates_to_the_tested_python_command() -> None:
    assert "deploy-backup" in _read("backup.sh")


def test_backup_refuses_when_the_service_is_active() -> None:
    """Gate 2: recusar, não apenas avisar. Um backup tirado durante escrita
    do DuckDB pode capturar o warehouse em estado intermediário.
    """
    code = _code("backup.sh")
    assert "is-active" in code
    # A recusa é um exit != 0, não um echo de aviso.
    assert "exit 1" in code


def test_backup_never_stops_the_service_itself() -> None:
    """Gate 2: parar o bot é decisão do operador — o script instrui, nunca
    executa.
    """
    assert not _executes("backup.sh", "systemctl stop")
    assert not _executes("backup.sh", "kill")
    assert not _executes("backup.sh", "pkill")
    # A sequência segura aparece só como instrução ao operador.
    assert "systemctl stop botgitgud" in _read("backup.sh")


def test_backup_stays_usable_where_systemd_does_not_exist() -> None:
    """Gate 2: a guarda é condicionada a `command -v systemctl`, senão o
    backup deixaria de ser testável offline/CI.
    """
    assert "command -v systemctl" in _code("backup.sh")


def test_backup_documents_the_safe_stop_backup_start_sequence() -> None:
    text = _read("backup.sh")
    assert "systemctl stop botgitgud" in text
    assert "systemctl start botgitgud" in text


def test_backup_uses_no_paid_cloud_storage() -> None:
    """Arquivo local é o destino; nenhum serviço pago é invocado. (O
    comentário do script cita S3/Object Storage para registrar a decisão.)
    """
    code = _code("backup.sh").lower()
    for service in ("s3", "object storage", "gsutil", "rclone", "aws "):
        assert service not in code


def test_restore_refuses_while_the_service_is_running() -> None:
    text = _read("restore.sh")
    assert "is-active" in text
    assert "exit 1" in text


def test_restore_requires_explicit_overwrite() -> None:
    assert "--overwrite" in _read("restore.sh")


def test_backup_archives_can_never_be_committed() -> None:
    """`deploy-backup` tem `--dest` relativo por padrão: rodado da raiz do
    repositório, ele criaria um arquivo com o warehouse inteiro dentro da
    árvore versionada. O .gitignore é o que impede isso virar um commit.
    """
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "backups/" in ignored
    assert "botgitgud-backup-*.tar.gz" in ignored


# -- documentação --------------------------------------------------------------


def test_doc_documents_the_full_cl8_flow() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    for step in (
        "bootstrap",
        "preflight",
        "backup",
        "restore",
    ):
        assert step in text, f"docs/linux-deployment.md não cobre {step}"


def test_doc_separates_offline_from_vm_dependent_actions() -> None:
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "offline" in text
    assert "vm" in text


def test_doc_references_every_deploy_script() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in SCRIPTS:
        assert name in text, f"docs/linux-deployment.md não menciona {name}"
