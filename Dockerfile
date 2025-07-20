FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg gettext-base && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/start.sh          # или /app/entrypoint.sh

ENTRYPOINT ["/app/start.sh"]
