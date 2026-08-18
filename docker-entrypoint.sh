#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  chown -R appuser:appuser /data
  exec gosu appuser "$@"
fi

exec "$@"
