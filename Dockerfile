# ---- Base image ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Minimal OS deps (jq for run.sh, curl for healthcheck, ca-certs for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Python deps (cache layer) ----
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefer-binary -r requirements.txt

# ---- App code ----
COPY homegpt /app/homegpt
COPY config.yaml icon.png logo.png README.md /app/

# Entrypoint script
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# ---- Runtime config ----
EXPOSE 8099

# Healthcheck (API status endpoint)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8099/api/status > /dev/null || exit 1

# IMPORTANT: run as root so we can read /data/options.json from Supervisor
USER root

CMD ["/usr/local/bin/run.sh"]
