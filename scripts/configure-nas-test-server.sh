#!/usr/bin/env bash

set -euo pipefail

MOUNT_ROOT="${NAS_MOUNT_ROOT:-/mnt/label-nas/maps}"
NAS_SHARE="//192.168.1.51/maps"
SMB_VERSION="${NAS_SMB_VERSION:-2.0}"
AUTH_SOURCE="${1:-}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "must run through sudo"
[ -n "$AUTH_SOURCE" ] || \
  fail "usage: configure-nas-test-server.sh <protected-credentials-file|--guest>"
case "$SMB_VERSION" in
  2.0|2.1|3.0|3.1.1) ;;
  *) fail "NAS_SMB_VERSION must be 2.0 or newer" ;;
esac
if [ "$AUTH_SOURCE" = "--guest" ]; then
  auth_option="guest"
else
  [ -f "$AUTH_SOURCE" ] || fail "credentials file was not found"
  mode=$(stat -c '%a' "$AUTH_SOURCE" 2>/dev/null || stat -f '%Lp' "$AUTH_SOURCE")
  [ "$mode" = "600" ] || fail "credentials file must have mode 600"
  auth_option="credentials=$AUTH_SOURCE"
fi
command -v mount.cifs >/dev/null 2>&1 || \
  fail "mount.cifs is missing; install cifs-utils in the approved server maintenance window"

install -d -m 0755 "$MOUNT_ROOT"
if mountpoint -q "$MOUNT_ROOT"; then
  source=$(findmnt -n -o SOURCE --target "$MOUNT_ROOT" || true)
  [ "$source" = "$NAS_SHARE" ] || fail "existing mount source is not $NAS_SHARE"
else
  mount -t cifs "$NAS_SHARE" "$MOUNT_ROOT" \
    -o "ro,$auth_option,vers=$SMB_VERSION,iocharset=utf8,noserverino"
fi

options=$(findmnt -n -o OPTIONS --target "$MOUNT_ROOT" || true)
printf '%s\n' ",$options," | grep -Eq ',ro,' || fail "NAS mount is not read-only"
printf 'NAS mounted read-only at %s\n' "$MOUNT_ROOT"
