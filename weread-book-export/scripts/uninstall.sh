#!/bin/sh
set -eu

WEREAD_USER_HOME=${WEREAD_EXPORT_HOME:-${HOME:?HOME 未设置}}
WEREAD_APP_SUPPORT="$WEREAD_USER_HOME/Library/Application Support/weread-book-export"
WEREAD_APP_BASE="$WEREAD_APP_SUPPORT/app"
WEREAD_CACHE_ROOT="$WEREAD_USER_HOME/Library/Caches/weread-book-export"
WEREAD_MANAGED_OUTPUT="$WEREAD_USER_HOME/Downloads/WeRead Exports"
WEREAD_YES=0
WEREAD_PURGE=0

for WEREAD_ARGUMENT in "$@"; do
  case "$WEREAD_ARGUMENT" in
    --yes) WEREAD_YES=1 ;;
    --purge-all) WEREAD_PURGE=1 ;;
    *) echo "未知参数：$WEREAD_ARGUMENT" >&2; exit 2 ;;
  esac
done

if [ "$WEREAD_YES" -ne 1 ]; then
  echo "卸载需要明确传入 --yes；彻底删除认证和托管输出另加 --purge-all。" >&2
  exit 10
fi

safe_remove_tree() {
  WEREAD_REMOVE_PATH=$1
  WEREAD_REMOVE_ROOT=$2
  case "$WEREAD_REMOVE_PATH" in
    "$WEREAD_REMOVE_ROOT"|"$WEREAD_REMOVE_ROOT"/*) ;;
    *) echo "拒绝删除范围外目录：$WEREAD_REMOVE_PATH" >&2; exit 40 ;;
  esac
  [ ! -L "$WEREAD_REMOVE_PATH" ] || { echo "拒绝递归删除符号链接" >&2; exit 40; }
  [ ! -e "$WEREAD_REMOVE_PATH" ] || find "$WEREAD_REMOVE_PATH" -depth -delete
}

if [ "$WEREAD_PURGE" -eq 1 ]; then
  WEREAD_CURRENT_BIN="$WEREAD_APP_BASE/current/bin/weread-book-export"
  if [ ! -x "$WEREAD_CURRENT_BIN" ]; then
    echo "缺少可用 CLI，无法安全校验并彻底删除托管输出。" >&2
    exit 40
  fi
  "$WEREAD_CURRENT_BIN" purge-all --yes --json >/dev/null
fi

if [ -f "$WEREAD_APP_SUPPORT/registrations.txt" ]; then
  while IFS= read -r WEREAD_LINK; do
    [ -n "$WEREAD_LINK" ] || continue
    if [ -L "$WEREAD_LINK" ]; then
      WEREAD_LINK_TARGET=$(readlink "$WEREAD_LINK")
      case "$WEREAD_LINK_TARGET" in
        "$WEREAD_APP_BASE"/*) unlink "$WEREAD_LINK" ;;
        *) echo "跳过非本工具链接：$WEREAD_LINK" >&2 ;;
      esac
    fi
  done < "$WEREAD_APP_SUPPORT/registrations.txt"
  unlink "$WEREAD_APP_SUPPORT/registrations.txt"
fi

safe_remove_tree "$WEREAD_APP_BASE" "$WEREAD_APP_SUPPORT"
for WEREAD_CACHE_ITEM in runtime uv python uv-cache tmp jobs logs; do
  if [ -e "$WEREAD_CACHE_ROOT/$WEREAD_CACHE_ITEM" ]; then
    safe_remove_tree "$WEREAD_CACHE_ROOT/$WEREAD_CACHE_ITEM" "$WEREAD_CACHE_ROOT"
  fi
done
[ ! -d "$WEREAD_CACHE_ROOT" ] || rmdir "$WEREAD_CACHE_ROOT" 2>/dev/null || true
[ ! -d "$WEREAD_APP_SUPPORT/locks" ] || safe_remove_tree "$WEREAD_APP_SUPPORT/locks" "$WEREAD_APP_SUPPORT"

if [ "$WEREAD_PURGE" -eq 1 ]; then
  [ ! -d "$WEREAD_APP_SUPPORT/auth" ] || safe_remove_tree "$WEREAD_APP_SUPPORT/auth" "$WEREAD_APP_SUPPORT"
  for WEREAD_STATE_FILE in \
    "$WEREAD_APP_SUPPORT/state.sqlite3" \
    "$WEREAD_APP_SUPPORT/state.sqlite3-wal" \
    "$WEREAD_APP_SUPPORT/state.sqlite3-shm"
  do
    [ ! -f "$WEREAD_STATE_FILE" ] || unlink "$WEREAD_STATE_FILE"
  done
  [ ! -d "$WEREAD_MANAGED_OUTPUT" ] || rmdir "$WEREAD_MANAGED_OUTPUT" 2>/dev/null || true
fi

[ ! -d "$WEREAD_APP_SUPPORT" ] || rmdir "$WEREAD_APP_SUPPORT" 2>/dev/null || true
echo "卸载完成。"
if [ "$WEREAD_PURGE" -eq 0 ]; then
  echo "已保留认证状态、SQLite 状态库和托管导出内容。"
fi
