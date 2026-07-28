#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
MODE="install"

usage() {
  echo "用法：install.sh [--check|--dry-run]"
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  case "$1" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "错误：install.sh 只允许在 macOS 上运行。" >&2
  exit 1
fi

select_python() {
  local candidate
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "$REPO_ROOT/.venv/bin/python"
    return
  fi
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
  done
  echo "错误：未检测到 Python 3.11/3.12。" >&2
  exit 1
}

PYTHON_BIN="$(select_python)"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'; then
  echo "错误：Python 必须为 3.11 或 3.12。" >&2
  exit 1
fi
command -v node >/dev/null 2>&1 || { echo "错误：未检测到 Node.js。" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "错误：未检测到 npm。" >&2; exit 1; }
NODE_MAJOR="$(node --version | sed -E 's/^v?([0-9]+).*/\1/')"
NPM_MAJOR="$(npm --version | sed -E 's/^([0-9]+).*/\1/')"
if [[ ! "$NODE_MAJOR" =~ ^[0-9]+$ ]]; then
  echo "错误：无法解析 Node.js 版本。" >&2
  exit 1
fi
if [[ ! "$NPM_MAJOR" =~ ^[0-9]+$ ]]; then
  echo "错误：无法解析 npm 版本。" >&2
  exit 1
fi
if (( NODE_MAJOR < 20 || NODE_MAJOR >= 27 )); then
  echo "错误：Node.js 必须为 20.x 至 26.x。" >&2
  exit 1
fi
if (( NPM_MAJOR < 10 || NPM_MAJOR >= 12 )); then
  echo "错误：npm 必须为 10.x 或 11.x。" >&2
  exit 1
fi
[[ -f "$REPO_ROOT/backend/requirements.txt" ]] || { echo "错误：缺少 backend/requirements.txt。" >&2; exit 1; }
[[ -f "$REPO_ROOT/frontend/package-lock.json" ]] || { echo "错误：缺少 frontend/package-lock.json。" >&2; exit 1; }

echo "Python=$("$PYTHON_BIN" --version 2>&1)"
echo "Node=$(node --version)"
echo "npm=$(npm --version)"
echo "门禁：Python 3.11/3.12；Node.js 20-26；npm 10/11。"

if [[ "$MODE" == "check" ]]; then
  [[ -x "$REPO_ROOT/.venv/bin/python" ]] || { echo "错误：仓库内 .venv 尚未创建。" >&2; exit 1; }
  [[ -f "$REPO_ROOT/frontend/dist/index.html" ]] || { echo "错误：前端生产构建不存在。" >&2; exit 1; }
  echo "安装状态检查通过；未安装、未构建、未访问网络。"
  exit 0
fi
if [[ "$MODE" == "dry-run" ]]; then
  echo "[DRY-RUN] $PYTHON_BIN -m venv $REPO_ROOT/.venv（仅在不存在时）"
  echo "[DRY-RUN] $REPO_ROOT/.venv/bin/python -m pip install -r $REPO_ROOT/backend/requirements.txt"
  echo "[DRY-RUN] (cd $REPO_ROOT/frontend && npm ci && npm run build)"
  echo "未创建文件、未安装、未构建、未访问网络。"
  exit 0
fi

if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$REPO_ROOT/.venv"
fi
"$REPO_ROOT/.venv/bin/python" -m pip install -r "$REPO_ROOT/backend/requirements.txt"
(
  cd -- "$REPO_ROOT/frontend"
  npm ci
  npm run build
)
echo "安装完成。未创建或覆盖任何 DATA_DIR 用户数据。"
