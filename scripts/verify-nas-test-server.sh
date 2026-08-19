#!/usr/bin/env bash

set -euo pipefail

MOUNT_ROOT="${NAS_MOUNT_ROOT:-/mnt/label-nas/maps}"
NAS_SHARE="//192.168.1.51/maps"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[ -d "$MOUNT_ROOT" ] || fail "NAS mount directory is missing: $MOUNT_ROOT"
mountpoint -q "$MOUNT_ROOT" || fail "NAS mount is not active: $MOUNT_ROOT"
[ "$(findmnt -n -o SOURCE --target "$MOUNT_ROOT")" = "$NAS_SHARE" ] || \
  fail "NAS mount source is not $NAS_SHARE"
options=$(findmnt -n -o OPTIONS --target "$MOUNT_ROOT" || true)
printf '%s\n' ",$options," | grep -Eq ',ro,' || fail "NAS mount is writable"
printf '%s\n' ",$options," | grep -Eq ',vers=(2\.0|2\.1|3\.0|3\.1\.1),' || \
  fail "NAS mount uses an unsupported or insecure SMB dialect"

required=(
  "采集任务交付文件/国圣坤/已处理样本3d&SU"
  "采集任务交付文件/小聪/模型评估流程/灵感图"
  "采集任务交付文件/林周金/模型迭代样本/灵感图-普通样本/第二批样本/人工评完"
  "采集任务交付文件/林周金/模型迭代样本/灵感图-普通样本"
)
for relative in "${required[@]}"; do
  [ -d "$MOUNT_ROOT/$relative" ] || fail "required NAS directory is missing: $relative"
  [ -r "$MOUNT_ROOT/$relative" ] || fail "required NAS directory is not readable: $relative"
done

printf 'NAS verification passed: %s (ro)\n' "$MOUNT_ROOT"
