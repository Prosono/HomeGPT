# ---- Base image ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Minimal OS deps for runtime + build of wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl jq ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user early
RUN useradd -u 10001 -m appuser

WORKDIR /app

# ---- Install Python dependencies (cache friendly) ----
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
 && pip install --prefer-binary -r requirements.txt

# ---- Copy application code ----
COPY homegpt /app/homegpt
COPY config.yaml icon.png logo.png README.md /app/

# Entrypoint script
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# Change ownership to non-root user
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8099

# Healthcheck for Home Assistant integration
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8099/api/status > /dev/null || exit 1

CMD ["/usr/local/bin/run.sh"]
