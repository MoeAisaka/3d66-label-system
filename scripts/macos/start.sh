#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

"$SCRIPT_DIR/doctor.sh"
export APP_HOST="${APP_HOST:-127.0.0.1}"
cd -- "$REPO_ROOT/backend"
exec "$PYTHON_BIN" -X utf8 -m app.launcher
