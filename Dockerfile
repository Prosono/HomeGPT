FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install tools + build deps for wheels when needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl jq ca-certificates build-essential libffi-dev python3-dev git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip & friends
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy requirements and install Python deps (add fastapi & uvicorn)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt \
    fastapi>=0.112 uvicorn>=0.30

# Copy the full homegpt package (api + app folders) into /app/homegpt
COPY homegpt /app/homegpt

# Copy entrypoint script
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# Optional: copy add-on assets if you need them in container
COPY config.yaml icon.png logo.png README.md /app/

# Internal port for HA ingress
EXPOSE 8099

CMD ["/usr/local/bin/run.sh"]
