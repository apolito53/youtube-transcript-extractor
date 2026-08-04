#!/usr/bin/env bash

set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_COMMAND="${APP_DIR}/.venv/bin/ytx"

if [[ ! -x "${APP_COMMAND}" ]]; then
  echo "The project virtualenv is missing or not installed." >&2
  echo "Run: ${APP_DIR}/.venv/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

exec "${APP_COMMAND}" "$@"
