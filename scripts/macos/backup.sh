#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "错误：backup.sh 只允许在 macOS 上运行。" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "错误：仓库内 .venv 不存在，请先运行 install.sh。" >&2
  exit 1
fi

PYTHONPATH="$REPO_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -X utf8 -m app.macos_deploy backup --repo-root "$REPO_ROOT" "$@"
