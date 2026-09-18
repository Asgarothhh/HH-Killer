#!/bin/sh
set -e

mkdir -p /app/logs

if [ "$(id -u)" -eq 0 ]; then
  chown -R 1000:1000 /app/logs || true
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=1000 --regid=1000 --init-groups -- "$@"
  fi
  exec runuser -u pwuser -- "$@"
fi

exec "$@"
