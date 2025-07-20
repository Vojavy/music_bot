#!/usr/bin/env sh
set -eu

TEMPLATE=/app/config-template.yaml
CONFIG=/app/config.yaml

command -v envsubst >/dev/null || {
  echo "envsubst not found (install gettext-base)" >&2
  exit 1
}

envsubst < "$TEMPLATE" > "$CONFIG"
exec python /app/main.py
