#!/bin/sh
set -eu

WEREAD_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
WEREAD_SOURCE_ROOT=$(CDPATH= cd -- "$WEREAD_SCRIPT_DIR/.." && pwd -P)
WEREAD_USER_HOME=${WEREAD_EXPORT_HOME:-${HOME:?HOME 未设置}}
WEREAD_APP_SUPPORT="$WEREAD_USER_HOME/Library/Application Support/weread-book-export"
WEREAD_APP_BASE="$WEREAD_APP_SUPPORT/app"
WEREAD_CACHE_ROOT="$WEREAD_USER_HOME/Library/Caches/weread-book-export"
WEREAD_VERSION=0.1.0
WEREAD_TARGET=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      [ "$#" -ge 2 ] || { echo "--target 需要目录" >&2; exit 2; }
      WEREAD_TARGET=$2
      shift 2
      ;;
    *)
      echo "未知参数：$1" >&2
      exit 2
      ;;
  esac
done

safe_remove_tree() {
  WEREAD_REMOVE_PATH=$1
  WEREAD_REMOVE_ROOT=$2
  case "$WEREAD_REMOVE_PATH" in
    "$WEREAD_REMOVE_ROOT"/*) ;;
    *) echo "拒绝删除范围外目录：$WEREAD_REMOVE_PATH" >&2; exit 40 ;;
  esac
  [ ! -L "$WEREAD_REMOVE_PATH" ] || { echo "拒绝递归删除符号链接" >&2; exit 40; }
  [ ! -e "$WEREAD_REMOVE_PATH" ] || find "$WEREAD_REMOVE_PATH" -depth -delete
}

copy_if_present() {
  WEREAD_COPY_ITEM=$1
  if [ -e "$WEREAD_SOURCE_ROOT/$WEREAD_COPY_ITEM" ]; then
    cp -R "$WEREAD_SOURCE_ROOT/$WEREAD_COPY_ITEM" "$WEREAD_INSTALL_TMP/$WEREAD_COPY_ITEM"
  fi
}

register_skill() {
  WEREAD_SKILLS_ROOT=$1
  mkdir -p "$WEREAD_SKILLS_ROOT"
  WEREAD_LINK="$WEREAD_SKILLS_ROOT/weread-book-export"
  if [ -L "$WEREAD_LINK" ]; then
    unlink "$WEREAD_LINK"
  elif [ -e "$WEREAD_LINK" ]; then
    echo "目标已存在且不是符号链接：$WEREAD_LINK" >&2
    exit 40
  fi
  ln -s "$WEREAD_APP_BASE/current" "$WEREAD_LINK"
  printf '%s\n' "$WEREAD_LINK" >> "$WEREAD_REGISTRATIONS_TMP"
}

remove_stale_registrations() {
  WEREAD_REGISTRATIONS_FILE="$WEREAD_APP_SUPPORT/registrations.txt"
  [ -f "$WEREAD_REGISTRATIONS_FILE" ] || return 0
  while IFS= read -r WEREAD_OLD_LINK || [ -n "$WEREAD_OLD_LINK" ]; do
    [ -L "$WEREAD_OLD_LINK" ] || continue
    WEREAD_OLD_TARGET=$(readlink "$WEREAD_OLD_LINK") || continue
    case "$WEREAD_OLD_TARGET" in
      "$WEREAD_APP_BASE"/*) unlink "$WEREAD_OLD_LINK" ;;
    esac
  done < "$WEREAD_REGISTRATIONS_FILE"
}

mkdir -p "$WEREAD_APP_BASE"
chmod 700 "$WEREAD_APP_SUPPORT" "$WEREAD_APP_BASE"
WEREAD_INSTALL_TMP="$WEREAD_APP_BASE/.install-$WEREAD_VERSION-$$"
mkdir "$WEREAD_INSTALL_TMP"
trap 'safe_remove_tree "$WEREAD_INSTALL_TMP" "$WEREAD_APP_BASE" 2>/dev/null || true' EXIT HUP INT TERM

for WEREAD_ITEM in pyproject.toml uv.lock .python-version runtime-manifest.json README.md requirements.txt; do
  copy_if_present "$WEREAD_ITEM"
done
for WEREAD_ITEM in src bin scripts SKILL.md agents references LICENSE THIRD_PARTY_NOTICES; do
  copy_if_present "$WEREAD_ITEM"
done
chmod 700 "$WEREAD_INSTALL_TMP/bin/weread-book-export" "$WEREAD_INSTALL_TMP/scripts/"*.sh

if [ "${WEREAD_EXPORT_TEST_MODE:-0}" = "1" ]; then
  [ "${WEREAD_EXPORT_TEST_SELF_CHECK_FAIL:-0}" != "1" ] || exit 40
else
  "$WEREAD_INSTALL_TMP/scripts/bootstrap.sh" --doctor-only >/dev/null
fi

WEREAD_VERSION_PATH="$WEREAD_APP_BASE/$WEREAD_VERSION"
WEREAD_OLD_SAME="$WEREAD_APP_BASE/.old-$WEREAD_VERSION-$$"
if [ -e "$WEREAD_VERSION_PATH" ]; then
  [ ! -L "$WEREAD_VERSION_PATH" ] || { echo "版本目录不能是符号链接" >&2; exit 40; }
  mv "$WEREAD_VERSION_PATH" "$WEREAD_OLD_SAME"
fi
mv "$WEREAD_INSTALL_TMP" "$WEREAD_VERSION_PATH"
trap - EXIT HUP INT TERM
ln -s "$WEREAD_VERSION" "$WEREAD_APP_BASE/current.new"
mv -h "$WEREAD_APP_BASE/current.new" "$WEREAD_APP_BASE/current"

remove_stale_registrations
WEREAD_REGISTRATIONS_TMP="$WEREAD_APP_SUPPORT/.registrations.$$"
: > "$WEREAD_REGISTRATIONS_TMP"
register_skill "$WEREAD_USER_HOME/.agents/skills"
for WEREAD_AGENT_ROOT in \
  "$WEREAD_USER_HOME/.claude/skills" \
  "$WEREAD_USER_HOME/.cursor/skills" \
  "$WEREAD_USER_HOME/.qwen/skills" \
  "$WEREAD_USER_HOME/.kimi/skills" \
  "$WEREAD_USER_HOME/.trae/skills"
do
  [ ! -d "$WEREAD_AGENT_ROOT" ] || register_skill "$WEREAD_AGENT_ROOT"
done
if [ -n "$WEREAD_TARGET" ]; then
  register_skill "$WEREAD_TARGET"
fi
mv "$WEREAD_REGISTRATIONS_TMP" "$WEREAD_APP_SUPPORT/registrations.txt"

if [ -e "$WEREAD_OLD_SAME" ]; then
  safe_remove_tree "$WEREAD_OLD_SAME" "$WEREAD_APP_BASE"
fi
for WEREAD_OLD_APP in "$WEREAD_APP_BASE"/*; do
  [ -e "$WEREAD_OLD_APP" ] || continue
  [ "$WEREAD_OLD_APP" = "$WEREAD_VERSION_PATH" ] && continue
  [ "$WEREAD_OLD_APP" = "$WEREAD_APP_BASE/current" ] && continue
  [ -L "$WEREAD_OLD_APP" ] && continue
  safe_remove_tree "$WEREAD_OLD_APP" "$WEREAD_APP_BASE"
done
if [ -d "$WEREAD_CACHE_ROOT/runtime" ]; then
  for WEREAD_OLD_RUNTIME in "$WEREAD_CACHE_ROOT/runtime"/*; do
    [ -e "$WEREAD_OLD_RUNTIME" ] || continue
    [ "$(basename "$WEREAD_OLD_RUNTIME")" = "$WEREAD_VERSION" ] && continue
    safe_remove_tree "$WEREAD_OLD_RUNTIME" "$WEREAD_CACHE_ROOT/runtime"
  done
fi

echo "安装完成：$WEREAD_VERSION_PATH"
echo "命令入口：$WEREAD_VERSION_PATH/bin/weread-book-export"
