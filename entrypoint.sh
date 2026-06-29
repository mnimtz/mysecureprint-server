#!/bin/bash
# =============================================================================
# mysecureprint-server — Docker Entrypoint
# =============================================================================
# Startet zwei Python-Services im selben Container:
#   - Web-UI + REST-API + iOS-App-Endpoints  (WEB_PORT, default 8080) — exposed
#   - MCP-Server fuer claude.ai / ChatGPT     (MCP_PORT, default 8765) — internal
#
# Der MCP-Server hoert nur auf 127.0.0.1 — externer Zugriff erfolgt
# AUSSCHLIESSLICH ueber den Reverse-Proxy in web/app.py, der durch den
# `mcp_enabled` DB-Setting freigeschaltet wird (Default: aus). Damit ist
# ein frisches Deployment OHNE oeffentlich erreichbaren MCP-Endpoint.
#
# Alle Secrets + SQLite-DB liegen in /data (muss als Volume gemountet sein).
# Konfiguration 100% via Environment-Variablen.
# =============================================================================

set -euo pipefail

log_info()  { printf '[%s] [INFO]  %s\n'  "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
log_warn()  { printf '[%s] [WARN]  %s\n'  "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
log_error() { printf '[%s] [ERROR] %s\n'  "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }

APP_VERSION="$(cat /app/VERSION 2>/dev/null || echo '0.0.0')"
export APP_VERSION

if [ ! -w /data ]; then
    log_error "/data ist nicht beschreibbar — bitte Volume korrekt mounten (chown 1000:1000)."
    exit 1
fi

if [ ! -f /data/fernet.key ]; then
    log_info "Erster Start — generiere Fernet-Key fuer DB-Verschluesselung..."
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /data/fernet.key
    chmod 600 /data/fernet.key
fi
export FERNET_KEY
FERNET_KEY="$(cat /data/fernet.key)"

export WEB_HOST="${WEB_HOST:-0.0.0.0}"
export WEB_PORT="${WEB_PORT:-8080}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8765}"
export MCP_LOG_LEVEL="${MCP_LOG_LEVEL:-info}"
export MCP_PUBLIC_URL="${MCP_PUBLIC_URL:-}"
MCP_PUBLIC_URL="${MCP_PUBLIC_URL%/}"

if [ -n "${MCP_PUBLIC_URL}" ]; then
    BASE="${MCP_PUBLIC_URL}"
else
    BASE="http://<host>:${WEB_PORT}"
fi

cat <<BANNER
================================================================================
  mysecureprint-server v${APP_VERSION}
  Web-UI:      http://<host>:${WEB_PORT}
  Health:      ${BASE}/health
  iOS-Pairing: ${BASE}/my/connect
================================================================================
BANNER

log_info "Starte MCP-Server (intern) auf ${MCP_HOST}:${MCP_PORT}..."
cd /app
python3 -X faulthandler -u /app/server.py >&1 &
MCP_PID=$!
cd - > /dev/null
log_info "MCP-Server PID: ${MCP_PID}"

cleanup() {
    log_info "SIGTERM erhalten — beende MCP (PID ${MCP_PID}) + Web-UI..."
    kill -TERM "${MCP_PID}" 2>/dev/null || true
    wait "${MCP_PID}" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

log_info "Starte Web-UI auf ${WEB_HOST}:${WEB_PORT}..."
log_info "Diag: uid=$(id -u) gid=$(id -g) user=$(whoami)"
log_info "Diag: /data perms: $(ls -ld /data 2>&1) — writable: $([ -w /data ] && echo yes || echo NO)"
log_info "Diag: python: $(python3 --version 2>&1)"
log_info "Diag: run.py exists: $([ -f /app/web/run.py ] && echo yes || echo NO)"
# stderr -> stdout damit Tracebacks im Azure-Log landen
exec python3 -X faulthandler -u /app/web/run.py 2>&1
