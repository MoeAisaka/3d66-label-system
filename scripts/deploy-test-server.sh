#!/usr/bin/env bash

set -u

PROJECT_DIR="/opt/3d66-label-system"
CONTAINER_NAME="3d66-label-system-test"
HEALTH_URL="http://127.0.0.1:8081/api/health"
HEALTH_ATTEMPTS=36
HEALTH_INTERVAL=5

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

compose_up() {
  sudo docker compose up -d --build
}

wait_for_health() {
  local attempt
  local state

  attempt=1
  while [ "$attempt" -le "$HEALTH_ATTEMPTS" ]; do
    state=$(sudo docker inspect \
      --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$CONTAINER_NAME" 2>/dev/null || true)
    if [ "$state" = "running healthy" ] && \
      curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null | \
        grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      return 0
    fi
    printf 'Waiting for health check (%s/%s): %s\n' \
      "$attempt" "$HEALTH_ATTEMPTS" "${state:-not running}"
    sleep "$HEALTH_INTERVAL"
    attempt=$((attempt + 1))
  done
  return 1
}

rollback() {
  local previous_commit=$1

  printf 'Rolling back to %s...\n' "$previous_commit" >&2
  git reset --hard "$previous_commit" || return 1
  compose_up || return 1
  wait_for_health || return 1
}

[ "$#" -eq 2 ] || fail "usage: deploy-test-server.sh <bundle-path> <commit>"
bundle_path=$1
target_commit=$2

case "$target_commit" in
  *[!0-9a-f]*|'') fail "commit must be a 40-character lowercase SHA" ;;
esac
[ "${#target_commit}" -eq 40 ] || fail "commit must be a 40-character lowercase SHA"
[ -f "$bundle_path" ] || fail "uploaded Git bundle was not found"
[ -d "$PROJECT_DIR/.git" ] || fail "server project Git repository was not found"

cd "$PROJECT_DIR" || fail "cannot enter server project directory"

tracked_changes=$(git status --porcelain --untracked-files=no) || \
  fail "cannot inspect server worktree"
[ -z "$tracked_changes" ] || fail "server worktree has tracked changes"

previous_commit=$(git rev-parse HEAD) || fail "cannot read current server commit"
git fetch "$bundle_path" main || fail "cannot import Codeup main bundle"
git cat-file -e "${target_commit}^{commit}" || fail "bundle does not contain requested commit"

printf 'Deploying %s (previous %s)...\n' "$target_commit" "$previous_commit"
git reset --hard "$target_commit" || fail "cannot switch server worktree to requested commit"

if ! compose_up || ! wait_for_health; then
  printf 'Deployment failed after updating code.\n' >&2
  if rollback "$previous_commit"; then
    rm -f -- "$bundle_path"
    fail "deployment failed; previous version was restored"
  fi
  fail "deployment and rollback both failed; inspect the server before retrying"
fi

rm -f -- "$bundle_path"
printf 'Deployment succeeded: %s\n' "$target_commit"
printf 'Container: %s (running healthy)\n' "$CONTAINER_NAME"
printf 'Health: %s\n' "$HEALTH_URL"
