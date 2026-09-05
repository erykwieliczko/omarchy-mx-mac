#!/bin/bash

set -euo pipefail

fail() {
  echo "build-dmg: $*" >&2
  exit 1
}

private_unsigned=false
if [[ ${1:-} == "--private-unsigned" ]]; then
  private_unsigned=true
  shift
fi
if (( $# != 2 )); then
  echo "usage: build-dmg.sh SIGNED_PACKAGE OUTPUT_DMG" >&2
  exit 64
fi

package=$1
output=$2
[[ -f $package && ! -L $package && $package == *.pkg ]] \
  || fail "input must be a regular .pkg file"
[[ $output == *.dmg ]] || fail "output must end in .dmg"
[[ ! -e $output && ! -L $output ]] || fail "refusing to overwrite $output"

package_directory=$(cd -- "$(dirname -- "$package")" && pwd -P)
package="$package_directory/$(basename -- "$package")"
mkdir -p -- "$(dirname -- "$output")"
output_directory=$(cd -- "$(dirname -- "$output")" && pwd -P)
output="$output_directory/$(basename -- "$output")"

# Keep the temporary image on the output filesystem for atomic publication.
work=$(mktemp -d "$output_directory/.omarchy-dmg.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/content"
staged_package="$work/content/$(basename -- "$package")"
ditto "$package" "$staged_package"
cmp -s "$package" "$staged_package" || fail "package changed while staging"
if [[ $private_unsigned == "false" ]]; then
  pkgutil --check-signature "$staged_package" || fail "package signature verification failed"
fi

cat >"$work/content/INSTALL.txt" <<'EOF'
Omarchy MX Mac Installer

Open the .pkg in this disk image to install the application and its helper.
Then open Omarchy MX Mac Installer from Applications.

The application checks this Mac and the release assets before preparing an
installation. The package installation and Linux installation are separate
steps.
EOF

if [[ $private_unsigned == "true" ]]; then
  cat >>"$work/content/INSTALL.txt" <<'EOF'

PRIVATE M4 TEST — unsigned package, unnotarized application.
This candidate supports MacBook Air M4 (Mac16,12 / J713), macOS 26.6.2.
It installs a console-first cleanroom kernel; accelerated graphics are not
included. Physical installation and paired Recovery boot remain unqualified.
Use macOS Privacy & Security > Open Anyway if macOS blocks this private build.
EOF
fi

hdiutil create -quiet -fs HFS+ -format UDZO \
  -volname "Omarchy MX Mac Installer" \
  -srcfolder "$work/content" "$work/installer.dmg"
hdiutil verify -quiet "$work/installer.dmg"

# A hard link also refuses a destination created after the initial check.
ln "$work/installer.dmg" "$output"
echo "DMG_BUILT: $output"
shasum -a 256 "$output"
