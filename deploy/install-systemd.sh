#!/usr/bin/env bash
# CL.8 -- instala a unit systemd de forma reproduzivel.
#
# Fluxo: valida a unit -> copia para /etc/systemd/system -> daemon-reload ->
# enable. NAO inicia o servico por padrao.
#
# Por que nao inicia sozinho: um `start` com .env incompleto sobe um processo
# que falha no boot por credenciais ausentes, e o supervisor trataria essa saida como crash -- gerando
# backoff e, na quinta tentativa, um restart storm. Melhor recusar cedo, com
# mensagem acionavel. `--start` existe para o caso em que o operador ja
# validou, e mesmo assim roda o preflight antes.
#
# NAO abre porta, NAO mexe em firewall e NAO altera SSH.
#
# Uso:
#   sudo ./deploy/install-systemd.sh [--repo-dir /opt/botgitgud] [--start]

set -euo pipefail

REPO_DIR="/opt/botgitgud"
DO_START=0
UNIT_NAME="botgitgud.service"
SYSTEMD_DIR="/etc/systemd/system"

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --start)
            DO_START=1
            shift
            ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
    esac
done

UNIT_SOURCE="${REPO_DIR}/deploy/systemd/${UNIT_NAME}"
UNIT_TARGET="${SYSTEMD_DIR}/${UNIT_NAME}"
PYTHON="${REPO_DIR}/.venv/bin/python"

log() { printf '[install-systemd] %s\n' "$*"; }
die() { printf '[install-systemd] erro: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    die "precisa de root (use sudo): escreve em ${SYSTEMD_DIR}"
fi

command -v systemctl >/dev/null 2>&1 || die "systemctl nao encontrado -- este host nao usa systemd"
[ -f "${UNIT_SOURCE}" ] || die "unit nao encontrada em ${UNIT_SOURCE}"

# -- validacao ANTES de instalar ----------------------------------------------

if command -v systemd-analyze >/dev/null 2>&1; then
    log "validando ${UNIT_SOURCE}"
    systemd-analyze verify "${UNIT_SOURCE}" || die "unit reprovada por systemd-analyze"
else
    log "systemd-analyze indisponivel -- seguindo com a validacao estatica do repositorio"
fi

grep -q 'botgitgud.cli supervise' "${UNIT_SOURCE}" \
    || die "ExecStart da unit nao aponta para o supervisor -- recusando instalar"

# -- instalacao ---------------------------------------------------------------

if [ -f "${UNIT_TARGET}" ] && cmp -s "${UNIT_SOURCE}" "${UNIT_TARGET}"; then
    log "unit ja instalada e identica -- nada a copiar"
else
    log "copiando ${UNIT_SOURCE} -> ${UNIT_TARGET}"
    install -m 0644 -o root -g root "${UNIT_SOURCE}" "${UNIT_TARGET}"
fi

log "systemctl daemon-reload"
systemctl daemon-reload

log "systemctl enable ${UNIT_NAME}"
systemctl enable "${UNIT_NAME}"

# -- start so com .env valido -------------------------------------------------

if [ "${DO_START}" -eq 0 ]; then
    log "instalada e habilitada. NAO iniciada (use --start, ou systemctl start ${UNIT_NAME})."
    log "antes de iniciar: configure ${REPO_DIR}/.env e rode ./deploy/preflight.sh"
    exit 0
fi

[ -x "${PYTHON}" ] || die "--start pedido, mas a venv nao existe em ${PYTHON}"

log "validando .env para start de producao (nenhum valor e impresso)"
if ! "${PYTHON}" -m botgitgud.cli deploy-validate-env \
        --env-file "${REPO_DIR}/.env"; then
    die ".env incompleto/invalido para producao -- start recusado."
fi

log "systemctl start ${UNIT_NAME}"
systemctl start "${UNIT_NAME}"
systemctl --no-pager status "${UNIT_NAME}" || true
