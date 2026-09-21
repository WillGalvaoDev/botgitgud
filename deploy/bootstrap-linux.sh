#!/usr/bin/env bash
# CL.8 -- bootstrap idempotente do host Linux para o BotGITGUD.
#
# Faz SOMENTE o que precisa de root ou de shell: usuario/grupo, diretorios,
# ownership, venv e instalacao do pacote. Toda politica que da para testar em
# pytest (validacao de .env, backup, preflight) vive em Python
# (src/botgitgud/ops/deploy.py, ops/preflight.py) e e chamada pelos outros
# scripts deste diretorio -- mesma divisao ja adotada em B6/CL.6.
#
# O que este script deliberadamente NAO faz:
#   - nao escreve segredo nenhum, nem inventa valor de .env;
#   - nao abre porta nem mexe em firewall;
#   - nao altera configuracao de SSH;
#   - nao inicia o servico (isso e install-systemd.sh, e so apos preflight);
#   - nao migra dados reais.
#
# Idempotente: rodar duas vezes seguidas nao muda nada na segunda.
#
# Uso:
#   sudo ./deploy/bootstrap-linux.sh [--repo-dir /opt/botgitgud] [--with-apt]

set -euo pipefail

REPO_DIR="/opt/botgitgud"
SERVICE_USER="botgitgud"
SERVICE_GROUP="botgitgud"
WITH_APT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --with-apt)
            # Opt-in explicito: por padrao este script VERIFICA dependencias de
            # sistema e falha com instrucao acionavel, em vez de mutar o host
            # sem o operador ter pedido.
            WITH_APT=1
            shift
            ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
    esac
done

DATA_DIR="${REPO_DIR}/data"
VENV_DIR="${REPO_DIR}/.venv"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap] erro: %s\n' "$*" >&2; exit 1; }

# -- root ---------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    die "precisa de root (use sudo): cria usuario de sistema e escreve em ${REPO_DIR}"
fi

# -- dependencias minimas de sistema ------------------------------------------

MISSING=""
for cmd in python3 git curl; do
    command -v "$cmd" >/dev/null 2>&1 || MISSING="${MISSING} ${cmd}"
done

# python3-venv nao expoe binario proprio: a checagem real e tentar o modulo.
if ! python3 -c 'import venv' >/dev/null 2>&1; then
    MISSING="${MISSING} python3-venv"
fi

if [ -n "${MISSING}" ]; then
    if [ "${WITH_APT}" -eq 1 ]; then
        log "instalando dependencias de sistema:${MISSING}"
        apt-get update
        # shellcheck disable=SC2086
        apt-get install -y python3 python3-venv git curl
    else
        die "dependencias ausentes:${MISSING}
  instale com: sudo apt-get install -y python3 python3-venv git curl
  ou repita este script com --with-apt"
    fi
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "python3 detectado: ${PY_VERSION}"
python3 - <<'PY' || die "Python 3.11+ e obrigatorio (ver requires-python em pyproject.toml)"
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY

# -- grupo e usuario de servico (idempotentes) --------------------------------

if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    log "grupo ${SERVICE_GROUP} ja existe"
else
    log "criando grupo ${SERVICE_GROUP}"
    groupadd --system "${SERVICE_GROUP}"
fi

if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    log "usuario ${SERVICE_USER} ja existe"
else
    # --no-create-home de proposito: a unit systemd usa ProtectHome=true, e um
    # servico sem home nenhum remove a classe inteira de erro em que o
    # ProtectHome esconde arquivos da propria aplicacao. Sem shell de login:
    # esta conta so roda um comando.
    log "criando usuario de sistema ${SERVICE_USER}"
    useradd --system --gid "${SERVICE_GROUP}" --no-create-home \
        --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# -- diretorios persistentes --------------------------------------------------

# -- contrato de staging do source tree ---------------------------------------
#
# Este script NAO traz o codigo: nao faz git clone, nao baixa nada, nao acessa
# a rede. O operador coloca a arvore em ${REPO_DIR} antes (git clone, rsync ou
# artefato de deploy) -- ver docs/linux-deployment.md.
#
# O contrato e verificado por MARCADORES explicitos, nao por "existe um
# diretorio": um ${REPO_DIR} parcial (rsync interrompido, checkout errado)
# falharia depois, no meio do pip install, com um erro que nao aponta para a
# causa. Falhar aqui e barato e diz exatamente o que fazer.

[ -d "${REPO_DIR}" ] || die "${REPO_DIR} nao existe.
  Coloque a arvore do repositorio ali antes de rodar este script:
    sudo git clone <repo-url> ${REPO_DIR}
  (ou rsync/artefato equivalente -- este script nao baixa codigo)"

for marker in pyproject.toml src/botgitgud/__init__.py src/botgitgud/cli.py \
              deploy/systemd/botgitgud.service; do
    [ -e "${REPO_DIR}/${marker}" ] || die "${REPO_DIR} nao e uma arvore completa do BotGITGUD: '${marker}' ausente.
  Verifique se o clone/copia terminou e se ${REPO_DIR} e a RAIZ do repositorio."
done

log "arvore do repositorio validada em ${REPO_DIR}"

# Mesma lista de PERSISTENT_DIRNAMES em src/botgitgud/ops/deploy.py -- o
# preflight reprova se divergirem, entao a duplicacao nao passa despercebida.
for sub in "" raw logs control ops ops/analysis-runs; do
    target="${DATA_DIR}${sub:+/$sub}"
    if [ -d "${target}" ]; then
        log "diretorio ja existe: ${target}"
    else
        log "criando ${target}"
        mkdir -p "${target}"
    fi
done

# -- ownership e permissoes ---------------------------------------------------

log "ajustando ownership de ${REPO_DIR} para ${SERVICE_USER}:${SERVICE_GROUP}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${REPO_DIR}"

# 750: o servico le/escreve; o grupo le; ninguem mais entra. data/ guarda o
# warehouse e dados operacionais -- nada disso e publico.
chmod 750 "${REPO_DIR}"
chmod -R 750 "${DATA_DIR}"

# .env, se ja existir, so pode ser lido pelo dono: e onde os tokens vivem.
if [ -f "${REPO_DIR}/.env" ]; then
    chmod 600 "${REPO_DIR}/.env"
    log ".env encontrado -- permissao ajustada para 600 (conteudo nao lido por este script)"
else
    log ".env AUSENTE -- copie deploy/env.example para ${REPO_DIR}/.env e preencha antes do start"
fi

# -- venv + instalacao do pacote ----------------------------------------------

if [ -x "${VENV_DIR}/bin/python" ]; then
    log "venv ja existe em ${VENV_DIR}"
else
    log "criando venv em ${VENV_DIR}"
    sudo -u "${SERVICE_USER}" python3 -m venv "${VENV_DIR}"
fi

# `pip install -e "${REPO_DIR}"` com o caminho EXPLICITO, nunca `-e .`: um
# `.` resolveria contra o cwd de quem chamou o script, e um sudo a partir de
# outro diretorio instalaria a arvore errada (ou falharia sem dizer por que).
# O mecanismo em si e o mesmo que o repositorio ja adota -- so o alvo e
# determinado, nao herdado.
log "instalando o pacote: pip install -e ${REPO_DIR} (caminho explicito, independente do cwd)"
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install -e "${REPO_DIR}"

# Prova que a instalacao aponta para ${REPO_DIR}, e nao para outra arvore que
# ja estivesse instalada na venv.
INSTALLED_AT="$(sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/python" -c \
    'import botgitgud, pathlib; print(pathlib.Path(botgitgud.__file__).resolve().parents[2])')"
if [ "${INSTALLED_AT}" != "${REPO_DIR}" ]; then
    die "o pacote instalado resolve para '${INSTALLED_AT}', nao para '${REPO_DIR}'.
  Isso indica uma instalacao anterior apontando para outra arvore. Recrie a venv:
    sudo rm -rf ${VENV_DIR} && sudo ${0} --repo-dir ${REPO_DIR}"
fi
log "pacote instalado a partir de ${INSTALLED_AT}"

log "bootstrap concluido."
log "proximos passos:"
log "  1. configure ${REPO_DIR}/.env (copie de deploy/env.example)"
log "  2. ./deploy/preflight.sh"
log "  3. sudo ./deploy/install-systemd.sh"
