#!/bin/bash
# Build a signed + notarized macOS installer package for the Omarchy Apple
# Silicon installer. The package installs the notarized app to /Applications and
# a system LaunchDaemon to /Library/LaunchDaemons, then loads the daemon in its
# postinstall — so the recipient enters one admin password (the macOS installer
# prompt) and never touches Login Items.
#
# Usage: build-pkg.sh --app <signed .app> --version <x.y.z> --out <output.pkg> \
#          [--plist <daemon plist>]
#
# The system daemon plist is derived from the plist the app embeds
# (Contents/Library/LaunchDaemons), rewriting its bundle-relative BundleProgram
# into an absolute Program under /Applications. Pass --plist only to supply a
# daemon plist that already carries an absolute Program; it is validated the
# same way.
set -euo pipefail

APP="" PLIST="" VERSION="" OUT=""
PRIVATE_UNSIGNED=false
INSTALLER_ID="Developer ID Installer: MARCELO DE BARROS ALCANTARA (T2C384FJBD)"
PKG_IDENTIFIER="com.omarchy.mx.installer.pkg"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$PKG_DIR/scripts"

while (( $# > 0 )); do
  case "$1" in
    --private-unsigned) PRIVATE_UNSIGNED=true; shift ;;
    --app) APP="$2"; shift 2 ;;
    --plist) PLIST="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --installer-identity) INSTALLER_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[[ -d "$APP" && -n "$VERSION" && -n "$OUT" ]] || {
  echo "usage: build-pkg.sh --app <app> --version <v> --out <pkg> [--plist <plist>]" >&2
  exit 64
}
[[ -z "$PLIST" || -f "$PLIST" ]] || { echo "build-pkg.sh: --plist is not a file: $PLIST" >&2; exit 64; }

if [[ $PRIVATE_UNSIGNED == "true" && -n $PLIST ]]; then
  echo "private packages must derive their exact client requirement" >&2
  exit 65
fi
[[ ! -e $OUT && ! -L $OUT ]] || { echo "refusing existing package" >&2; exit 65; }
work="$(mktemp -d /tmp/omarchy-pkg.XXXXXX)"
trap 'rm -rf "$work"' EXIT
root="$work/root"
mkdir -p "$root/Applications" "$root/Library/LaunchDaemons"

# Payload: the notarized app and the system daemon plist.
/usr/bin/ditto "$APP" "$root/Applications/$(basename "$APP")"

# Derive the daemon plist from the app's embedded copy (absolute Program under
# /Applications), or validate a supplied one the same way. A daemon whose
# Program is missing, relative, or points at nothing inside the staged app
# cannot be loaded by launchd, so that is a build failure, not a warning.
daemon_plist="$work/com.omarchy.mx.installer.helper.plist"
if [[ -n "$PLIST" ]]; then
  /bin/cp "$PLIST" "$daemon_plist"
else
  "$PKG_DIR/derive-daemon-plist" "$APP" "$daemon_plist" /Applications >/dev/null
fi
if [[ $PRIVATE_UNSIGNED == "true" ]]; then
  staged_app="$root/Applications/$(basename "$APP")"
  helper="$staged_app/Contents/Resources/omarchy-apple-installer-helper"
  app_cdhash=$(/usr/bin/codesign -d --verbose=4 "$staged_app" 2>&1 | awk -F= '$1 == "CDHash" {print $2}')
  helper_cdhash=$(/usr/bin/codesign -d --verbose=4 "$helper" 2>&1 | awk -F= '$1 == "CDHash" {print $2}')
  [[ $app_cdhash =~ ^[0-9a-f]{40}$ && $helper_cdhash =~ ^[0-9a-f]{40}$ ]] || exit 65
  client_requirement="identifier \"com.omarchy.mx.installer\" and cdhash H\"$app_cdhash\""
  helper_requirement="identifier \"com.omarchy.mx.installer.helper\" and cdhash H\"$helper_cdhash\""
  actual_helper_requirement=$(/usr/bin/plutil -extract helper_code_signing_requirement raw -o - "$staged_app/Contents/Resources/Release/release.json")
  [[ $actual_helper_requirement == "$helper_requirement" ]] || { echo "private app does not pin this helper" >&2; exit 65; }
  /usr/bin/codesign --verify --deep --strict "$staged_app"
  /usr/bin/codesign --verify --strict -R="$client_requirement" "$staged_app"
  /usr/bin/codesign --verify --strict -R="$helper_requirement" "$helper"
  /usr/bin/plutil -replace EnvironmentVariables.OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT -string "$client_requirement" "$daemon_plist"
fi
prog="$(/usr/bin/plutil -extract Program raw -o - "$daemon_plist" 2>/dev/null || true)"
[[ $prog == /Applications/* ]] || {
  echo "build-pkg.sh: daemon Program must be an absolute path under /Applications (got '${prog:-<missing>}')" >&2
  exit 65
}
[[ -f "$root$prog" ]] || {
  echo "build-pkg.sh: daemon Program does not exist inside the staged app: $prog" >&2
  exit 65
}
# The helper compiles this requirement at launch and exits EX_CONFIG when it
# cannot, and launchd then respawns it forever while the app waits on a reply
# that never comes. The repo template carries a placeholder that build-app.sh
# replaces in the app's embedded copy; a pkg built from the template itself
# shipped exactly that failure once, so refuse anything but a real requirement.
requirement="$(/usr/bin/plutil -extract EnvironmentVariables.OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT raw -o - "$daemon_plist" 2>/dev/null || true)"
case "$requirement" in
  "" | *REPLACED_DURING_PACKAGING*)
    echo "build-pkg.sh: daemon plist has no client code-signing requirement (got '${requirement:-<missing>}'); use the app's embedded plist, not the repo template" >&2
    exit 65
    ;;
esac
/usr/bin/install -m 0644 "$daemon_plist" "$root/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist"
echo "daemon client requirement: $requirement"
echo "daemon Program: $prog"

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

product_arguments=(--package "$comp" --identifier "$PKG_IDENTIFIER" --version "$VERSION")
if [[ $PRIVATE_UNSIGNED == "false" ]]; then
  product_arguments+=(--sign "$INSTALLER_ID")
fi
echo "=== productbuild ==="
/usr/bin/productbuild "${product_arguments[@]}" "$OUT"

echo "=== verify signature ==="
if [[ $PRIVATE_UNSIGNED == "false" ]]; then
  /usr/sbin/pkgutil --check-signature "$OUT" | head -6
else
  echo "PRIVATE TEST PACKAGE: unsigned and unnotarized"
fi

echo "PKG_BUILT: $OUT"
echo "sha256: $(/usr/bin/shasum -a 256 "$OUT" | awk '{print $1}')"
