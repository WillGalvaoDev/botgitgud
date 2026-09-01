#!/usr/bin/env bash
# CL.9B -- casca fina sobre `botgitgud.cli deploy-activation-readiness`
# (src/botgitgud/ops/activation.py), onde toda a lógica de verificação
# realmente vive e é testada offline.
#
# Agrega os gates de CL.8 (host/env/systemd/loopback) + CL.9A (domínio,
# contrato do Caddyfile) + o gate NOVO desta etapa (coerência entre o
# domínio do Caddy e REPORT_PUBLIC_BASE_URL).
#
# NUNCA muta nada: nenhum systemctl, nenhuma chamada de rede, nenhum DNS,
# nenhuma alteração de firewall, nenhuma emissão de capability real. Ver
# docs/activation-runbook.md para a sequência MANUAL completa (que
# inclui passos mutáveis) -- este script cobre só a fatia validável offline
# dela.
#
# Uso:
#   ./deploy/activation-readiness.sh --domain example.duckdns.org \
#       --caddyfile /etc/caddy/Caddyfile

set -euo pipefail

REPO_DIR="/opt/botgitgud"
DOMAIN=""
CADDYFILE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --caddyfile)
            CADDYFILE="$2"
            shift 2
            ;;
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "erro: argumento desconhecido: $1" >&2
            exit 2
            ;;
    esac
done

PYTHON="${REPO_DIR}/.venv/bin/python"

log() { printf '[activation-readiness] %s\n' "$*"; }

if [ -z "${DOMAIN}" ]; then
    echo "[activation-readiness] erro: --domain é obrigatório." >&2
    exit 2
fi
if [ -z "${CADDYFILE}" ]; then
    echo "[activation-readiness] erro: --caddyfile é obrigatório." >&2
    exit 2
fi
if [ ! -x "${PYTHON}" ]; then
    echo "[activation-readiness] erro: venv não encontrada em ${PYTHON}" >&2
    exit 1
fi

log "rodando gates offline (CL.8 + CL.9A + coerência de domínio)"
exec "${PYTHON}" -m botgitgud.cli deploy-activation-readiness \
    --data-dir "${REPO_DIR}/data" \
    --env-file "${REPO_DIR}/.env" \
    --repo-root "${REPO_DIR}" \
    --domain "${DOMAIN}" \
    --caddyfile "${CADDYFILE}"
