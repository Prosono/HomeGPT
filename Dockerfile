# ---- Base image ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Minimal OS deps (jq for run.sh, ca-certs for HTTPS, build tools only if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create app user early so file ownership can be cached
RUN useradd -u 10001 -m appuser

WORKDIR /app

# ---- Python deps (kept in a dedicated cacheable layer) ----
# Copy only requirements first to maximize cache hits
COPY requirements.txt /app/requirements.txt

# If you pin FastAPI/uvicorn/pyyaml in requirements.txt, delete the line below.
# Otherwise we add them here so they’re also cached separately.
RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefer-binary -r requirements.txt fastapi>=0.112 uvicorn>=0.30 pyyaml

# ---- App code (these layers change the most) ----
# Copy your package *after* deps so edits don’t bust the pip cache
# Expecting repo layout like:
#   /homegpt/
#     api/...
#     app/...
#     frontend/...
COPY homegpt /app/homegpt

# Static assets or add-on metadata (optional)
# (Safe to copy with code; they change rarely but aren’t big)
COPY config.yaml icon.png logo.png README.md /app/

# Entrypoint
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# Non-root for safety
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8099

# Healthcheck hits the API status endpoint; HA will show unhealthy if it fails
# (Don’t be scared of curl here—busybox curl comes from the base, but we added curl above)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8099/api/status > /dev/null || exit 1

CMD ["/usr/local/bin/run.sh"]
