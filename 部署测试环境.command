#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$REPO_ROOT"
exec python3 scripts/deploy-test.py "$@"
