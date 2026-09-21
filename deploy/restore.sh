#!/usr/bin/env bash
# CL.8 -- restore do estado duravel. Casca fina sobre `botgitgud.cli
# deploy-restore` (src/botgitgud/ops/deploy.py).
#
# Recusa sobrescrever arquivo existente sem --overwrite: restaurar por cima
# de um warehouse vivo so e recuperavel com outro backup, entao exigir um
# gesto explicito e o ponto.
#
# O servico DEVE estar parado: o DuckDB tem contrato de dono unico, e
# substituir o arquivo por baixo de uma conexao aberta corrompe o estado.
#
# Uso:
#   sudo systemctl stop botgitgud
#   ./deploy/restore.sh <arquivo.tar.gz> [--repo-dir /opt/botgitgud] [--overwrite]

set -euo pipefail

REPO_DIR="/opt/botgitgud"
ARCHIVE=""
OVERWRITE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --overwrite)
            OVERWRITE="--overwrite"
            shift
            ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        -*)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
        *)
            ARCHIVE="$1"
            shift
            ;;
    esac
done

PYTHON="${REPO_DIR}/.venv/bin/python"

if [ -z "${ARCHIVE}" ]; then
    echo "erro: informe o arquivo de backup. Uso: ./deploy/restore.sh <arquivo.tar.gz>" >&2
    exit 2
fi

if [ ! -x "${PYTHON}" ]; then
    echo "[restore] erro: venv nao encontrada em ${PYTHON}" >&2
    exit 1
fi

if systemctl is-active --quiet botgitgud.service 2>/dev/null; then
    echo "[restore] erro: botgitgud.service esta ATIVO." >&2
    echo "[restore]        pare o servico antes: sudo systemctl stop botgitgud" >&2
    exit 1
fi

# shellcheck disable=SC2086
exec "${PYTHON}" -m botgitgud.cli deploy-restore "${ARCHIVE}" \
    --data-dir "${REPO_DIR}/data" ${OVERWRITE}
