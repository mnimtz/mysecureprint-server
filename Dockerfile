# syntax=docker/dockerfile:1.7
# =============================================================================
# mysecureprint-server — Slim print backend for the MySecurePrint iOS app
# =============================================================================
# Multi-Stage Build:
#   1. builder  — kompiliert Python-Wheels (pyodbc/pymssql brauchen dev-Header)
#   2. runtime  — schlankes Debian-Slim mit Runtime-Deps inkl.
#                  LibreOffice-core + Ghostscript fuer Dokumenten-Konvertierung
#                  (Word/JPG/PDF -> PCL XL).
#
# Ziel-Plattformen: linux/amd64, linux/arm64 (gebaut via buildx in CI)
# =============================================================================

ARG PYTHON_VERSION=3.13

# -----------------------------------------------------------------------------
# Stage 1: Builder — Python-Wheels kompilieren
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        unixodbc-dev \
        freetds-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY src/requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: Runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080 \
    MCP_LOG_LEVEL=info \
    DB_PATH=/data/printix_multi.db \
    TZ=Europe/Berlin

RUN apt-get update && apt-get install -y --no-install-recommends \
        # FreeTDS ODBC (Azure SQL)
        unixodbc \
        tdsodbc \
        freetds-bin \
        # LibreOffice-core fuer Dokument-Konvertierung (docx/xlsx/pptx/odt -> PDF)
        libreoffice-core \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-dejavu \
        # Ghostscript fuer PDF -> PCL XL (pxlcolor) Konvertierung
        ghostscript \
        # certbot fuer /admin/auto-tls (Let's Encrypt)
        certbot \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# FreeTDS-Treiber registrieren
RUN if ! grep -q "\[FreeTDS\]" /etc/odbcinst.ini 2>/dev/null; then \
        DRIVER=$(find /usr/lib -name "libtdsodbc.so*" 2>/dev/null | head -1); \
        if [ -n "$DRIVER" ]; then \
            printf "[FreeTDS]\nDescription=FreeTDS ODBC Driver\nDriver=%s\nSetup=%s\nFileUsage=1\n" \
                   "$DRIVER" "$DRIVER" >> /etc/odbcinst.ini; \
        fi; \
    fi

COPY --from=builder /wheels /wheels
COPY src/requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt

WORKDIR /app
COPY src/ /app/
COPY VERSION /app/VERSION
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN groupadd --system --gid 1000 printix \
    && useradd --system --uid 1000 --gid printix --home-dir /app --shell /bin/bash printix \
    && mkdir -p /data \
    && chown -R printix:printix /data /app

USER printix

VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${WEB_PORT}/health" > /dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
