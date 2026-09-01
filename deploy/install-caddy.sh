#!/usr/bin/env bash
# CL.9A -- instala/configura o Caddy de forma reproduzivel. Casca fina sobre
# `botgitgud.cli deploy-render-caddyfile` (src/botgitgud/ops/caddy_config.py),
# onde a validacao do dominio e a substituicao do template vivem e sao
# testadas offline.
#
# Fluxo: exige --domain -> renderiza o Caddyfile a partir do template ->
# valida com `caddy validate` (quando o binario existe) -> instala de forma
# idempotente, NUNCA sobrescrevendo config existente e diferente sem
# --force -> reload OPCIONAL (--reload), nunca automatico.
#
# NAO instala o pacote Caddy a menos que --install-package seja passado
# explicitamente (exige rede -- nunca executado em teste). NAO abre porta,
# NAO mexe em firewall, NAO inicia o botgitgud.service, NAO usa
# certificado local/self-signed.
#
# Uso:
#   sudo ./deploy/install-caddy.sh --domain example.duckdns.org
#   sudo ./deploy/install-caddy.sh --domain example.duckdns.org --install-package --reload

set -euo pipefail

REPO_DIR="/opt/botgitgud"
DOMAIN=""
FORCE=0
INSTALL_PACKAGE=0
DO_RELOAD=0
CADDY_CONFIG_TARGET="/etc/caddy/Caddyfile"

while [ $# -gt 0 ]; do
    case "$1" in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --repo-dir)
            REPO_DIR="$2"
            shift 2
            ;;
        --target)
            CADDY_CONFIG_TARGET="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --install-package)
            # Opt-in explicito: exige rede (repositorio apt do Caddy).
            # Nunca executado por teste algum -- so quando o operador pede.
            INSTALL_PACKAGE=1
            shift
            ;;
        --reload)
            DO_RELOAD=1
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

PYTHON="${REPO_DIR}/.venv/bin/python"
TEMPLATE="${REPO_DIR}/deploy/caddy/Caddyfile.template"

log() { printf '[install-caddy] %s\n' "$*"; }
die() { printf '[install-caddy] erro: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
    die "precisa de root (use sudo): escreve em ${CADDY_CONFIG_TARGET}"
fi

# -- dominio obrigatorio -------------------------------------------------------
#
# NUNCA um default operacional aqui. Sem --domain, a mensagem aponta para a
# CL.9B (DNS) -- este script nao inventa nem adivinha o dominio real.
if [ -z "${DOMAIN}" ]; then
    die "--domain e obrigatorio.
  CL.9A nao define um dominio de producao: ele so existe depois que a CL.9B
  (DNS/DuckDNS) apontar para o IP publico da VM. Rode este script de novo
  com --domain <dominio-real> quando o DNS existir."
fi

[ -x "${PYTHON}" ] || die "venv nao encontrada em ${PYTHON} -- rode deploy/bootstrap-linux.sh antes"
[ -f "${TEMPLATE}" ] || die "template nao encontrado em ${TEMPLATE}"

# -- instalacao opcional do pacote (rede, opt-in) ------------------------------

if [ "${INSTALL_PACKAGE}" -eq 1 ]; then
    if command -v caddy >/dev/null 2>&1; then
        log "pacote caddy ja instalado ($(caddy version 2>/dev/null || echo versao-desconhecida))"
    else
        log "instalando o pacote caddy via apt (exige rede -- repositorio oficial)"
        apt-get update
        apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
            | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
            | tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update
        apt-get install -y caddy
    fi
fi

# -- renderizacao (Python, testado offline) ------------------------------------

STAGING="$(mktemp)"
trap 'rm -f "${STAGING}"' EXIT

log "renderizando Caddyfile para o dominio configurado"
if ! "${PYTHON}" -m botgitgud.cli deploy-render-caddyfile \
        --domain "${DOMAIN}" --template "${TEMPLATE}" --out "${STAGING}"; then
    die "renderizacao do Caddyfile falhou -- ver mensagem acima"
fi

# -- validacao ANTES de instalar -----------------------------------------------

if command -v caddy >/dev/null 2>&1; then
    log "validando com 'caddy validate'"
    caddy validate --config "${STAGING}" --adapter caddyfile \
        || die "Caddyfile reprovado por 'caddy validate' -- nada foi instalado"
else
    log "binario 'caddy' indisponivel -- validacao sintatica pulada (nao e falha; "
    log "rode --install-package numa maquina com rede, ou instale manualmente antes)"
fi

# -- instalacao idempotente, nunca sobrescreve config diferente sem --force ----

if [ -f "${CADDY_CONFIG_TARGET}" ] && cmp -s "${STAGING}" "${CADDY_CONFIG_TARGET}"; then
    log "config ja instalada e identica -- nada a copiar"
elif [ -f "${CADDY_CONFIG_TARGET}" ] && [ "${FORCE}" -ne 1 ]; then
    die "${CADDY_CONFIG_TARGET} ja existe e e DIFERENTE do Caddyfile gerado.
  Revise a diferenca antes de decidir:
    diff -u ${CADDY_CONFIG_TARGET} ${STAGING}
  Repita com --force para substituir deliberadamente."
else
    mkdir -p "$(dirname "${CADDY_CONFIG_TARGET}")"
    install -m 0644 -o root -g root "${STAGING}" "${CADDY_CONFIG_TARGET}"
    log "Caddyfile instalado em ${CADDY_CONFIG_TARGET}"
fi

# -- reload OPCIONAL, nunca automatico ------------------------------------------

if [ "${DO_RELOAD}" -eq 0 ]; then
    log "instalado. NAO recarregado (use --reload, ou 'systemctl reload caddy' manualmente)."
    exit 0
fi

command -v systemctl >/dev/null 2>&1 || die "--reload pedido, mas systemctl nao encontrado"
log "systemctl reload caddy"
systemctl reload caddy
