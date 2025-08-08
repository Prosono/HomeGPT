FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Tools + build deps for wheels when needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl jq ca-certificates build-essential libffi-dev python3-dev git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip tooling
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Install FastAPI + Uvicorn for ingress dashboard
RUN pip install --no-cache-dir fastapi uvicorn[standard]

# Copy your main app files
COPY app/ /app/app/

# Copy new dashboard backend and static UI
COPY server.py /app/server.py
COPY static/ /app/static/

# Copy run script
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

# Default command (your existing entrypoint)
CMD ["/usr/local/bin/run.sh"]
