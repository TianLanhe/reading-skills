#!/bin/sh
set -eu

WEREAD_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
WEREAD_SOURCE_ROOT=$(CDPATH= cd -- "$WEREAD_SCRIPT_DIR/.." && pwd -P)
WEREAD_VERSION=0.1.0
WEREAD_DIST="$WEREAD_SOURCE_ROOT/dist"
WEREAD_PACKAGE="$WEREAD_DIST/weread-book-export-$WEREAD_VERSION.zip"
WEREAD_CHECKSUM="$WEREAD_PACKAGE.sha256"
WEREAD_PACKAGE_TMP=$(mktemp -d "${TMPDIR:-/tmp}/weread-package.XXXXXX")
WEREAD_PACKAGE_ROOT="$WEREAD_PACKAGE_TMP/weread-book-export"

trap 'find "$WEREAD_PACKAGE_TMP" -depth -delete 2>/dev/null || true' EXIT HUP INT TERM
mkdir -p "$WEREAD_PACKAGE_ROOT" "$WEREAD_DIST"

for WEREAD_FILE in \
  .python-version \
  LICENSE \
  README.md \
  SKILL.md \
  THIRD_PARTY_NOTICES \
  pyproject.toml \
  requirements.txt \
  runtime-manifest.json \
  uv.lock
do
  cp "$WEREAD_SOURCE_ROOT/$WEREAD_FILE" "$WEREAD_PACKAGE_ROOT/$WEREAD_FILE"
done

for WEREAD_DIRECTORY in agents bin references scripts src; do
  cp -R "$WEREAD_SOURCE_ROOT/$WEREAD_DIRECTORY" "$WEREAD_PACKAGE_ROOT/$WEREAD_DIRECTORY"
done

find "$WEREAD_PACKAGE_ROOT" -name '*.pyc' -type f -delete
find "$WEREAD_PACKAGE_ROOT" -depth -type d -name __pycache__ -delete
find "$WEREAD_PACKAGE_ROOT" -name .DS_Store -type f -delete
find "$WEREAD_PACKAGE_ROOT" -exec touch -t 202608260000 {} +

[ ! -e "$WEREAD_PACKAGE" ] || unlink "$WEREAD_PACKAGE"
[ ! -e "$WEREAD_CHECKSUM" ] || unlink "$WEREAD_CHECKSUM"
(
  cd "$WEREAD_PACKAGE_TMP"
  COPYFILE_DISABLE=1 zip -X -q -r "$WEREAD_PACKAGE" weread-book-export
)
WEREAD_HASH=$(shasum -a 256 "$WEREAD_PACKAGE" | awk '{print $1}')
printf '%s  %s\n' "$WEREAD_HASH" "$(basename "$WEREAD_PACKAGE")" > "$WEREAD_CHECKSUM"

find "$WEREAD_PACKAGE_TMP" -depth -delete
trap - EXIT HUP INT TERM
echo "$WEREAD_PACKAGE"
echo "$WEREAD_HASH"
