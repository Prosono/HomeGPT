FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Tools + build deps for wheels when needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl jq ca-certificates build-essential libffi-dev python3-dev git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Newer pip grabs prebuilt wheels more often
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt /app/
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY app/ /app/
COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

CMD ["/usr/local/bin/run.sh"]