# ---- Base image ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Minimal OS deps (jq for run.sh, ca-certs for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl jq ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -u 10001 -m appuser
WORKDIR /app

# ---- Python deps (cache friendly) ----
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
 && pip install --prefer-binary -r requirements.txt

# ---- App code ----
# Expecting repo layout:
# homegpt/
#   api/...
#   app/...
#   (api/frontend with index.html + static/)
COPY homegpt /app/homegpt

# Add-on metadata / assets (optional)
COPY config.yaml icon.png logo.png README.md /app/

# Entrypoint
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh \
 && chown -R appuser:appuser /app

USER appuser
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8099/api/status > /dev/null || exit 1

CMD ["/usr/local/bin/run.sh"]
