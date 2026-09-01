#!/usr/bin/env bash
# CL.8 -- preflight de deploy. Casca fina sobre `botgitgud.cli
# deploy-preflight` (src/botgitgud/ops/preflight.py), que e onde as
# verificacoes de verdade moram e onde a suite as exercita.
#
# Acrescenta apenas o que so o shell sabe fazer: escolher o interpretador da
# venv e, quando systemd existir na maquina, validar a unit com
# `systemd-analyze verify` (indisponivel no Windows/CI, por isso opcional).
#
# NAO faz chamada de rede. NAO inicia o servico. Sai != 0 em qualquer falha.
#
# Uso:
#   ./deploy/preflight.sh [--repo-dir /opt/botgitgud]

set -euo pipefail

REPO_DIR="/opt/botgitgud"

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,14p' "$0"
            exit 0
            ;;
        *)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
    esac
done

PYTHON="${REPO_DIR}/.venv/bin/python"
UNIT_SOURCE="${REPO_DIR}/deploy/systemd/botgitgud.service"

log() { printf '[preflight] %s\n' "$*"; }

if [ ! -x "${PYTHON}" ]; then
    echo "[preflight] erro: venv nao encontrada em ${PYTHON} -- rode deploy/bootstrap-linux.sh antes" >&2
    exit 1
fi

STATUS=0

log "verificacoes Python (offline)"
"${PYTHON}" -m botgitgud.cli deploy-preflight \
    --data-dir "${REPO_DIR}/data" \
    --env-file "${REPO_DIR}/.env" \
    --repo-root "${REPO_DIR}" || STATUS=1

# systemd-analyze so existe onde ha systemd; sua ausencia nunca reprova o
# preflight (a suite roda em maquina sem systemd, e a unit ja tem guardas
# estaticos proprios em tests/unit/test_systemd_unit.py).
if command -v systemd-analyze >/dev/null 2>&1; then
    log "validando unit com systemd-analyze verify"
    if systemd-analyze verify "${UNIT_SOURCE}"; then
        log "unit valida"
    else
        log "unit REPROVADA por systemd-analyze"
        STATUS=1
    fi
else
    log "systemd-analyze indisponivel -- validacao da unit pulada (nao e falha)"
fi

if [ "${STATUS}" -eq 0 ]; then
    log "PASSOU"
else
    log "FALHOU -- corrija os itens acima antes de iniciar o servico"
fi

exit "${STATUS}"
