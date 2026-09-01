#!/usr/bin/env bash
# CL.8 -- backup do estado duravel. Casca fina sobre `botgitgud.cli
# deploy-backup` (src/botgitgud/ops/deploy.py), onde a allowlist de conteudo
# vive e e testada.
#
# Inclui: warehouse.duckdb, raw/, reports/, ops/, logs/
# Exclui:  control/ (stop.request, bot.pid, supervisor.lock -- estado efemero
#          e perigoso de restaurar), ops-snapshot.json, spells.json, a venv e
#          o codigo-fonte.
#
# Destino e um arquivo .tar.gz LOCAL: sem S3, sem OCI Object Storage, sem
# nenhum servico pago. Copiar o arquivo para outra maquina depois e decisao
# do operador, fora do escopo deste script.
#
# Recomendado (mas nao obrigatorio) parar o servico antes: o DuckDB tem
# contrato de dono unico, e um backup tirado durante escrita pode capturar um
# warehouse em estado intermediario.
#
# Uso:
#   ./deploy/backup.sh [--repo-dir /opt/botgitgud] [--dest /opt/botgitgud/backups]

set -euo pipefail

REPO_DIR="/opt/botgitgud"
DEST=""

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --dest)
            DEST="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,22p' "$0"
            exit 0
            ;;
        *)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
    esac
done

PYTHON="${REPO_DIR}/.venv/bin/python"
DEST="${DEST:-${REPO_DIR}/backups}"

if [ ! -x "${PYTHON}" ]; then
    echo "[backup] erro: venv nao encontrada em ${PYTHON}" >&2
    exit 1
fi

# O DuckDB tem contrato de dono unico: um backup tirado enquanto o bot escreve
# pode capturar o warehouse em estado intermediario -- um arquivo que parece
# valido e so falha quando alguem precisar dele. Recusar e o comportamento
# correto; hot backup consistente e outro problema, fora do escopo desta fase.
#
# NUNCA para o servico sozinho: parar o bot e decisao do operador.
#
# Onde nao ha systemd (maquina de desenvolvimento, CI), `command -v` falha e
# esta guarda e pulada -- o backup continua testavel offline.
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet botgitgud.service; then
        echo "[backup] erro: botgitgud.service esta ATIVO -- backup recusado." >&2
        echo "[backup]        o warehouse pode estar sendo escrito agora (DuckDB e dono unico)." >&2
        echo "[backup]        sequencia segura:" >&2
        echo "[backup]          sudo systemctl stop botgitgud" >&2
        echo "[backup]          ./deploy/backup.sh" >&2
        echo "[backup]          sudo systemctl start botgitgud" >&2
        exit 1
    fi
fi

exec "${PYTHON}" -m botgitgud.cli deploy-backup \
    --data-dir "${REPO_DIR}/data" \
    --dest "${DEST}"
