#!/usr/bin/env bash
# Build the Shorekeep macOS app bundle.
#
# Env:
#   PYTHON              interpreter to use (default: .venv/bin/python or python3)
#   SKIP_INSTALL=1      skip pip install step
#   SIGN_IDENTITY       codesign identity, e.g. "Developer ID Application: Name (TEAMID)"
#   NOTARY_PROFILE      notarytool keychain profile name for notarization
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(tr -d '[:space:]' < VERSION)"
export SHOREKEEP_VERSION="$VERSION"

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

# PyInstaller needs an .icns for the bundle icon; derive one from the 512px PNG.
ICNS="assets/brand/shorekeep.icns"
if [ ! -f "$ICNS" ]; then
  ICONSET="build/shorekeep.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  SRC="assets/brand/shorekeep-512.png"
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$ICNS"
fi

"$PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build \
  infra/macos/Shorekeep.spec

APP="dist/Shorekeep.app"
if [ ! -d "$APP" ]; then
  echo "Build failed: $APP was not produced." >&2
  exit 1
fi

# PyInstaller's BUNDLE signs ad-hoc by default; replace it with a real identity when provided.
if [ -n "${SIGN_IDENTITY:-}" ]; then
  echo "Signing with: $SIGN_IDENTITY"
  codesign --force --deep --options runtime --timestamp \
    --sign "$SIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

ZIP="dist/Shorekeep-macos-$VERSION.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "Submitting for notarization with profile: $NOTARY_PROFILE"
  xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
fi

shasum -a 256 "$ZIP" > "$ZIP.sha256"

echo "App:  $APP"
echo "Zip:  $ZIP"
echo "Sums: $ZIP.sha256"
