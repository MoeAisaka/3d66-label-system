#!/usr/bin/env bash

set -euo pipefail

MOUNT_ROOT="${NAS_MOUNT_ROOT:-/mnt/label-nas/maps}"
NAS_SHARE="//192.168.1.51/maps"
CREDENTIALS_FILE="${1:-}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "must run through sudo"
[ -n "$CREDENTIALS_FILE" ] || fail "usage: configure-nas-test-server.sh <protected-credentials-file>"
[ -f "$CREDENTIALS_FILE" ] || fail "credentials file was not found"
mode=$(stat -c '%a' "$CREDENTIALS_FILE" 2>/dev/null || stat -f '%Lp' "$CREDENTIALS_FILE")
[ "$mode" = "600" ] || \
  fail "credentials file must have mode 600"
command -v mount.cifs >/dev/null 2>&1 || \
  fail "mount.cifs is missing; install cifs-utils in the approved server maintenance window"

install -d -m 0755 "$MOUNT_ROOT"
if mountpoint -q "$MOUNT_ROOT"; then
  source=$(findmnt -n -o SOURCE --target "$MOUNT_ROOT" || true)
  [ "$source" = "$NAS_SHARE" ] || fail "existing mount source is not $NAS_SHARE"
else
  mount -t cifs "$NAS_SHARE" "$MOUNT_ROOT" \
    -o "ro,credentials=$CREDENTIALS_FILE,vers=3.0,iocharset=utf8,noserverino"
fi

options=$(findmnt -n -o OPTIONS --target "$MOUNT_ROOT" || true)
printf '%s\n' ",$options," | grep -Eq ',ro,' || fail "NAS mount is not read-only"
printf 'NAS mounted read-only at %s\n' "$MOUNT_ROOT"
