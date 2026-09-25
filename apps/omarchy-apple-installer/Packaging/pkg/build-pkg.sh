#!/bin/bash
# Optional signed package wrapper for the standalone app. No persistent helper.
set -euo pipefail

APP="" VERSION="" OUT=""
INSTALLER_ID="Developer ID Installer: MARCELO DE BARROS ALCANTARA (T2C384FJBD)"
PKG_IDENTIFIER="com.omarchy.mx.installer.pkg"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$PKG_DIR/scripts"

while (( $# > 0 )); do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --installer-identity) INSTALLER_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[[ -d "$APP" && -n "$VERSION" && -n "$OUT" ]] || {
  echo "usage: build-pkg.sh --app <app> --version <v> --out <pkg>" >&2
  exit 64
}

work="$(mktemp -d /tmp/omarchy-pkg.XXXXXX)"
trap 'rm -rf "$work"' EXIT
root="$work/root"
mkdir -p "$root/Applications"

# The app prompts for its temporary helper only when an operation needs it.
/usr/bin/ditto "$APP" "$root/Applications/$(basename "$APP")"

# Pin the app to /Applications. Without this, PackageKit "relocates" the
# install onto any existing copy of the bundle it can find anywhere on disk
# (it did exactly that onto a stale test copy in /Users/Shared), leaving
# /Applications empty and the daemon pointing at nothing.
component_plist="$work/component.plist"
/usr/bin/pkgbuild --analyze --root "$root" "$component_plist"
/usr/bin/plutil -replace 0.BundleIsRelocatable -bool false "$component_plist"

echo "=== pkgbuild (component) ==="
comp="$work/component.pkg"
/usr/bin/pkgbuild \
  --root "$root" \
  --component-plist "$component_plist" \
  --scripts "$SCRIPTS" \
  --identifier "$PKG_IDENTIFIER" \
  --version "$VERSION" \
  --install-location / \
  --ownership recommended \
  "$comp"

echo "=== productbuild (signed distribution) ==="
/usr/bin/productbuild \
  --package "$comp" \
  --identifier "$PKG_IDENTIFIER" \
  --version "$VERSION" \
  --sign "$INSTALLER_ID" \
  "$OUT"

echo "=== verify signature ==="
/usr/sbin/pkgutil --check-signature "$OUT" | head -6

echo "PKG_BUILT: $OUT"
echo "sha256: $(/usr/bin/shasum -a 256 "$OUT" | awk '{print $1}')"
