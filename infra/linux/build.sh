#!/usr/bin/env bash
# Build the Shorekeep Linux bundle.
#
# Env:
#   PYTHON          interpreter to use (default: .venv/bin/python or python3)
#   SKIP_INSTALL=1  skip pip install step
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(tr -d '[:space:]' < VERSION)"
export SHOREKEEP_VERSION="$VERSION"
ARCH="$(uname -m)"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

if [ "${SKIP_INSTALL:-0}" != "1" ]; then
  "$PYTHON" -m pip install -q -e services/backend -e "services/desktop[build]"
fi

"$PYTHON" infra/brand_assets.py
"$PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build \
  infra/linux/Shorekeep.spec

if [ ! -x "dist/Shorekeep/Shorekeep" ]; then
  echo "Build failed: dist/Shorekeep/Shorekeep was not produced." >&2
  exit 1
fi

TARBALL="dist/Shorekeep-linux-$ARCH-$VERSION.tar.gz"
rm -f "$TARBALL"
tar -czf "$TARBALL" -C dist Shorekeep

(cd dist && sha256sum "Shorekeep-linux-$ARCH-$VERSION.tar.gz") > "$TARBALL.sha256"

echo "Bundle: dist/Shorekeep"
echo "Tarball: $TARBALL"
echo "Sums:    $TARBALL.sha256"
