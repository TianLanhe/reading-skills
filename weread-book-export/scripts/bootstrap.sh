#!/bin/sh
set -eu

WEREAD_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
WEREAD_APP_ROOT=$(CDPATH= cd -- "$WEREAD_SCRIPT_DIR/.." && pwd -P)
WEREAD_USER_HOME=${WEREAD_EXPORT_HOME:-${HOME:?HOME 未设置}}
WEREAD_CACHE_ROOT="$WEREAD_USER_HOME/Library/Caches/weread-book-export"
WEREAD_RUNTIME_ROOT="$WEREAD_CACHE_ROOT/runtime/0.1.0"
WEREAD_UV_ROOT="$WEREAD_CACHE_ROOT/uv/0.12.1"
WEREAD_UV_BIN="$WEREAD_UV_ROOT/uv"
WEREAD_PYTHON_ROOT="$WEREAD_CACHE_ROOT/python"
WEREAD_UV_CACHE="$WEREAD_CACHE_ROOT/uv-cache"
WEREAD_TMP_ROOT="$WEREAD_CACHE_ROOT/tmp"

if [ "$(uname -s)" != "Darwin" ]; then
  echo '{"status":"failed","reason":"unsupported_macos","message":"仅支持 macOS"}' >&2
  exit 40
fi

WEREAD_MAC_MAJOR=$(sw_vers -productVersion | awk -F. '{print $1}')
if [ "$WEREAD_MAC_MAJOR" -lt 13 ]; then
  echo '{"status":"failed","reason":"unsupported_macos","message":"需要 macOS 13 或更高版本"}' >&2
  exit 40
fi

WEREAD_ARCH=$(uname -m)
case "$WEREAD_ARCH" in
  arm64)
    WEREAD_UV_ARCHIVE=uv-aarch64-apple-darwin.tar.gz
    WEREAD_UV_DIRECTORY=uv-aarch64-apple-darwin
    WEREAD_UV_SHA=77d2906988e8074fd43f2f329ec452ebbf9b0c257ba1c66451c71de70a6baf42
    ;;
  x86_64)
    WEREAD_UV_ARCHIVE=uv-x86_64-apple-darwin.tar.gz
    WEREAD_UV_DIRECTORY=uv-x86_64-apple-darwin
    WEREAD_UV_SHA=69d9f9a00337f25a50dcb13882052da08b8469bac11091c98c5694c3c6721467
    ;;
  *)
    echo '{"status":"failed","reason":"unsupported_arch","message":"仅支持 arm64 和 x86_64"}' >&2
    exit 40
    ;;
esac

mkdir -p \
  "$WEREAD_RUNTIME_ROOT" \
  "$WEREAD_UV_ROOT" \
  "$WEREAD_PYTHON_ROOT" \
  "$WEREAD_UV_CACHE" \
  "$WEREAD_TMP_ROOT"
chmod 700 "$WEREAD_CACHE_ROOT" "$WEREAD_RUNTIME_ROOT" 2>/dev/null || true

if [ ! -x "$WEREAD_UV_BIN" ]; then
  WEREAD_DOWNLOAD_DIR=$(mktemp -d "$WEREAD_TMP_ROOT/uv.XXXXXX")
  trap 'find "$WEREAD_DOWNLOAD_DIR" -depth -delete 2>/dev/null || true' EXIT HUP INT TERM
  curl -fL --retry 3 --proto '=https' --tlsv1.2 \
    -o "$WEREAD_DOWNLOAD_DIR/$WEREAD_UV_ARCHIVE" \
    "https://github.com/astral-sh/uv/releases/download/0.12.1/$WEREAD_UV_ARCHIVE"
  WEREAD_ACTUAL_SHA=$(shasum -a 256 "$WEREAD_DOWNLOAD_DIR/$WEREAD_UV_ARCHIVE" | awk '{print $1}')
  if [ "$WEREAD_ACTUAL_SHA" != "$WEREAD_UV_SHA" ]; then
    echo '{"status":"failed","reason":"uv_checksum_mismatch","message":"uv 下载校验失败"}' >&2
    exit 40
  fi
  tar -xzf "$WEREAD_DOWNLOAD_DIR/$WEREAD_UV_ARCHIVE" -C "$WEREAD_DOWNLOAD_DIR"
  install -m 700 "$WEREAD_DOWNLOAD_DIR/$WEREAD_UV_DIRECTORY/uv" "$WEREAD_UV_BIN"
  find "$WEREAD_DOWNLOAD_DIR" -depth -delete
  trap - EXIT HUP INT TERM
fi

export UV_PROJECT_ENVIRONMENT="$WEREAD_RUNTIME_ROOT/venv"
export UV_PYTHON_INSTALL_DIR="$WEREAD_PYTHON_ROOT"
export UV_CACHE_DIR="$WEREAD_UV_CACHE"
export UV_LINK_MODE=copy
export UV_MANAGED_PYTHON=1
export UV_NO_PROGRESS=1
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
export TMPDIR="$WEREAD_TMP_ROOT"

"$WEREAD_UV_BIN" sync \
  --project "$WEREAD_APP_ROOT" \
  --frozen \
  --no-dev \
  --no-editable \
  --python 3.13.14

if [ "${1:-}" = "--doctor-only" ]; then
  shift
  set -- doctor --json "$@"
fi

exec "$WEREAD_RUNTIME_ROOT/venv/bin/weread-book-export" "$@"
