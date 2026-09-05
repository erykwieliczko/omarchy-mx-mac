#!/bin/bash

set -euo pipefail

fail() {
  echo "build-app: $*" >&2
  exit 1
}

usage() {
  echo "usage: build-app.sh RELEASE_DIRECTORY OUTPUT_DIRECTORY" >&2
  exit 64
}

(( $# == 2 )) || usage

script_directory="$({ cd "$(dirname "$0")" && pwd -P; })"
package_directory="$({ cd "$script_directory/.." && pwd -P; })"
release_directory="$({ cd "$1" && pwd -P; })"
output_directory="$2"

[[ -d $release_directory && ! -L $release_directory ]] \
  || fail "release directory must be a real directory"
[[ $output_directory == /* ]] \
  || output_directory="$package_directory/$output_directory"

build_jobs="${OMARCHY_BUILD_JOBS:-10}"
[[ $build_jobs =~ ^[1-9][0-9]*$ ]] \
  || fail "OMARCHY_BUILD_JOBS must be a positive integer"
export CARGO_BUILD_JOBS="$build_jobs"

marketing_version="${OMARCHY_APP_VERSION:-0.6.0}"
build_number="${OMARCHY_APP_BUILD_NUMBER:-6}"
signing_identity="${OMARCHY_APP_SIGNING_IDENTITY:--}"
team_identifier="${OMARCHY_TEAM_ID:-}"
private_package="${OMARCHY_PRIVATE_PACKAGE:-0}"
bundled_release="${OMARCHY_BUNDLED_RELEASE:-0}"
engine_only="${OMARCHY_ENGINE_ONLY_RELEASE:-0}"
private_http_origin="${OMARCHY_PRIVATE_HTTP_ORIGIN:-}"
if [[ -n $private_http_origin ]]; then
  [[ $private_package == "1" && $engine_only == "1" ]] || fail "HTTP is only available for a private engine-only build"
fi
[[ $engine_only == "0" || $engine_only == "1" ]] || fail "invalid engine-only release flag"
[[ $engine_only != "1" || $bundled_release == "0" ]] || fail "engine-only and bundled release are mutually exclusive"
[[ $private_package == "0" || $private_package == "1" ]] || fail "invalid private package flag"
[[ $bundled_release == "0" || $bundled_release == "1" ]] || fail "invalid bundled release flag"
if [[ $private_package == "1" ]]; then
  [[ $signing_identity == "-" && ( $bundled_release == "1" || $engine_only == "1" ) ]] || fail "private package requires ad-hoc signing and a sealed engine"
fi

[[ $marketing_version =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)*$ ]] \
  || fail "OMARCHY_APP_VERSION has an invalid format"
[[ $build_number =~ ^[1-9][0-9]*$ ]] \
  || fail "OMARCHY_APP_BUILD_NUMBER must be a positive integer"

app_identifier="com.omarchy.mx.installer"
helper_identifier="com.omarchy.mx.installer.helper"
app_name="Omarchy MX Mac Installer.app"
app_executable_name="OmarchyAppleInstallerApp"
helper_executable_name="omarchy-apple-installer-helper"
daemon_plist_name="$helper_identifier.plist"
engine_file_name="installer-v0.9.0-omarchy.14.tar.gz"
engine_digest="9e9277384b6c9e8b269cc79b1b24df7bfcdcbb898a596a677b74d1d18050aebe"

if [[ $private_package == "1" ]]; then
  # The installed system daemon gets the final app cdhash after app signing.
  # The embedded registration path deliberately admits no executable.
  client_requirement="identifier \"$app_identifier\" and cdhash H\"0000000000000000000000000000000000000000\""
  helper_requirement="identifier \"$helper_identifier\" and cdhash H\"0000000000000000000000000000000000000000\""
  timestamp_arguments=(--timestamp=none)
elif [[ $signing_identity == "-" ]]; then
  client_requirement="identifier \"$app_identifier\""
  helper_requirement="identifier \"$helper_identifier\""
  timestamp_arguments=(--timestamp=none)
else
  [[ $team_identifier =~ ^[A-Z0-9]{10}$ ]] \
    || fail "OMARCHY_TEAM_ID is required for named signing identities"
  client_requirement="anchor apple generic and identifier \"$app_identifier\" and certificate leaf[subject.OU] = \"$team_identifier\""
  helper_requirement="anchor apple generic and identifier \"$helper_identifier\" and certificate leaf[subject.OU] = \"$team_identifier\""
  if [[ $signing_identity == "Developer ID Application:"* ]]; then
    timestamp_arguments=(--timestamp)
  elif [[ $signing_identity =~ ^[0-9A-Fa-f]{40}$ ]] \
    && security find-identity -p codesigning -v \
      | grep -i "$signing_identity" | grep -q "Developer ID Application"; then
    # Signing by SHA-1 fingerprint (duplicate same-name certificates make
    # names ambiguous): resolve the certificate kind from the keychain so
    # Developer ID builds keep the secure timestamp notarization requires.
    timestamp_arguments=(--timestamp)
  else
    timestamp_arguments=(--timestamp=none)
  fi
fi

release_descriptor="$release_directory/release.json"
trust_root="$release_directory/trust-root.ed25519.pub"
sealed_catalog="$release_directory/catalog.json"
sealed_catalog_signature="$release_directory/catalog.json.sig"
[[ -f $release_descriptor && ! -L $release_descriptor ]] \
  || fail "release.json is missing or unsafe"
[[ -f $trust_root && ! -L $trust_root ]] \
  || fail "trust-root.ed25519.pub is missing or unsafe"
(( $(stat -f %z "$release_descriptor") <= 65536 )) \
  || fail "release.json exceeds 65536 bytes"
(( $(stat -f %z "$trust_root") == 32 )) \
  || fail "trust-root.ed25519.pub must contain exactly 32 bytes"

sealed_catalog_available=false
if [[ -e $sealed_catalog || -L $sealed_catalog \
  || -e $sealed_catalog_signature || -L $sealed_catalog_signature ]]; then
  if [[ ! -f $sealed_catalog || -L $sealed_catalog ]]; then
    fail "catalog.json is missing or unsafe"
  fi
  if [[ ! -f $sealed_catalog_signature || -L $sealed_catalog_signature ]]; then
    fail "catalog.json.sig is missing or unsafe"
  fi
  catalog_size="$(stat -f %z "$sealed_catalog")"
  if (( catalog_size <= 0 || catalog_size > 1048576 )); then
    fail "catalog.json is empty or exceeds 1048576 bytes"
  fi
  if (( $(stat -f %z "$sealed_catalog_signature") != 64 )); then
    fail "catalog.json.sig must contain exactly 64 bytes"
  fi
  sealed_catalog_available=true
fi

if [[ $signing_identity != "-" && $sealed_catalog_available != "true" ]]; then
  fail "installation-capable releases require an embedded catalog for independent helper validation"
fi

descriptor_schema="$(plutil -extract schema_version raw -o - "$release_descriptor")"
descriptor_service="$(plutil -extract helper_mach_service_name raw -o - "$release_descriptor")"
descriptor_requirement="$(plutil -extract helper_code_signing_requirement raw -o - "$release_descriptor")"
descriptor_fingerprint="$(plutil -extract trust_root_fingerprint raw -o - "$release_descriptor")"
if [[ $descriptor_schema != "1" ]]; then
  fail "release.json schema_version must be 1"
fi
if [[ $descriptor_service != "$helper_identifier" ]]; then
  fail "release.json helper service does not match the compiled product"
fi
if [[ $descriptor_requirement != "$helper_requirement" ]]; then
  fail "release.json helper signing requirement does not match this build"
fi
actual_fingerprint="sha256:$(/usr/bin/shasum -a 256 "$trust_root" | awk '{print $1}')"
if [[ $descriptor_fingerprint != "$actual_fingerprint" ]]; then
  fail "release.json trust root fingerprint does not match the public key"
fi

if [[ $bundled_release == "1" || $engine_only == "1" ]]; then
  [[ $sealed_catalog_available == "true" ]] || fail "bundled release requires a sealed catalog"
  verification_arguments=("$release_directory")
  [[ $engine_only == "0" ]] || verification_arguments+=(--engine-only)
  /usr/bin/python3 "$script_directory/verify-bundled-assets.py" "${verification_arguments[@]}"
  engine_file_name=$(plutil -extract models.0.engineArtifact.fileName raw -o - "$sealed_catalog")
  engine_digest=$(plutil -extract models.0.engineDigest raw -o - "$sealed_catalog")
  engine_digest=${engine_digest#sha256:}
  engine_source="$release_directory/Assets/$engine_file_name"
else
  engine_source="$package_directory/Engine/artifacts/$engine_file_name"
fi
if [[ ! -f $engine_source || -L $engine_source ]]; then
  fail "the pinned validation engine artifact is missing"
fi
actual_engine_digest="$(/usr/bin/shasum -a 256 "$engine_source" | awk '{print $1}')"
if [[ $actual_engine_digest != "$engine_digest" ]]; then
  fail "the pinned validation engine digest is incorrect"
fi

mkdir -p "$output_directory"
final_app="$output_directory/$app_name"
[[ ! -e $final_app ]] \
  || fail "refusing to overwrite existing app: $final_app"

swift_tool="$(xcrun --find swift)"
swift_arguments=(--configuration release)
if [[ -n $private_http_origin ]]; then
  swift_arguments+=(-Xswiftc -DOMARCHY_PRIVATE_HTTP)
fi
if [[ -n ${OMARCHY_SWIFT_WORK_DIRECTORY:-} ]]; then
  swift_arguments+=(--cache-path "$OMARCHY_SWIFT_WORK_DIRECTORY/swift-cache" --config-path "$OMARCHY_SWIFT_WORK_DIRECTORY/swift-config" --security-path "$OMARCHY_SWIFT_WORK_DIRECTORY/swift-security")
  export CLANG_MODULE_CACHE_PATH="$OMARCHY_SWIFT_WORK_DIRECTORY/clang-cache"
fi
(
  cd "$package_directory"
  "$swift_tool" build \
    "${swift_arguments[@]}" --jobs "$build_jobs"
)
binary_directory="$({
  cd "$package_directory"
  "$swift_tool" build "${swift_arguments[@]}" --show-bin-path
})"

app_binary="$binary_directory/$app_executable_name"
helper_binary="$binary_directory/OmarchyAppleInstallerHelper"
[[ -x $app_binary ]] || fail "app executable was not built"
[[ -x $helper_binary ]] || fail "helper executable was not built"

assembly_root="$(mktemp -d "$output_directory/.omarchy-app.XXXXXX")"
trap 'rm -rf "$assembly_root"' EXIT
assembled_app="$assembly_root/$app_name"
contents="$assembled_app/Contents"
resources="$contents/Resources"

mkdir -p \
  "$contents/MacOS" \
  "$resources/Release" \
  "$resources/Engine/artifacts" \
  "$contents/Library/LaunchDaemons"

install -m 0755 "$app_binary" "$contents/MacOS/$app_executable_name"
install -m 0755 "$helper_binary" "$resources/$helper_executable_name"
install -m 0444 "$release_descriptor" "$resources/Release/release.json"
install -m 0444 "$trust_root" "$resources/Release/trust-root.ed25519.pub"
if [[ $sealed_catalog_available == "true" ]]; then
  install -m 0444 "$sealed_catalog" "$resources/Release/catalog.json"
  install -m 0444 \
    "$sealed_catalog_signature" \
    "$resources/Release/catalog.json.sig"
fi
if [[ $engine_only == "1" ]]; then
  mkdir "$resources/Release/Assets"
  install -m 0444 "$engine_source" "$resources/Release/Assets/$engine_file_name"
elif [[ $bundled_release == "1" ]]; then
  mkdir "$resources/Release/Assets"
  for asset in "$release_directory/Assets/"*; do
    install -m 0444 "$asset" "$resources/Release/Assets/${asset##*/}"
  done
else
  install -m 0444 "$engine_source" "$resources/Engine/artifacts/$engine_file_name"
fi
install -m 0444 \
  "$script_directory/OmarchyInstaller.icns" \
  "$resources/OmarchyInstaller.icns"
install -m 0444 "$script_directory/Info.plist" "$contents/Info.plist"
install -m 0444 \
  "$script_directory/$daemon_plist_name" \
  "$contents/Library/LaunchDaemons/$daemon_plist_name"

chmod 0644 "$contents/Info.plist"
if [[ -n $private_http_origin ]]; then
  /usr/bin/python3 - "$contents/Info.plist" "$private_http_origin" <<'PYHTTP'
import ipaddress, plistlib, sys
from urllib.parse import urlsplit
path, origin = sys.argv[1:]
url = urlsplit(origin)
if (url.scheme != 'http' or not url.hostname or url.username or url.password
        or url.path not in ('', '/') or url.query or url.fragment):
    raise SystemExit('private HTTP origin must be an explicit origin without credentials or path')
address = ipaddress.ip_address(url.hostname)
allowed = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '127.0.0.0/8')]
if not any(address in network for network in allowed):
    raise SystemExit('private HTTP origin must use a local or tailnet IPv4 address')
with open(path, 'rb') as reader:
    info = plistlib.load(reader)
info['OmarchyPrivateHTTPOrigin'] = origin
info['NSAppTransportSecurity'] = {'NSExceptionDomains': {url.hostname: {'NSExceptionAllowsInsecureHTTPLoads': True}}}
info['NSLocalNetworkUsageDescription'] = 'Download the private Omarchy test image from your build machine.'
with open(path, 'wb') as writer:
    plistlib.dump(info, writer)
PYHTTP
fi
chmod 0644 "$contents/Info.plist"
chmod 0644 "$contents/Library/LaunchDaemons/$daemon_plist_name"
plutil -replace CFBundleShortVersionString \
  -string "$marketing_version" "$contents/Info.plist"
plutil -replace CFBundleVersion \
  -string "$build_number" "$contents/Info.plist"
plutil -replace \
  EnvironmentVariables.OMARCHY_CLIENT_CODE_SIGNING_REQUIREMENT \
  -string "$client_requirement" \
  "$contents/Library/LaunchDaemons/$daemon_plist_name"
plutil -lint \
  "$contents/Info.plist" \
  "$contents/Library/LaunchDaemons/$daemon_plist_name" >/dev/null

codesign --force --sign "$signing_identity" \
  "${timestamp_arguments[@]}" \
  --options runtime \
  --identifier "$helper_identifier" \
  "$resources/$helper_executable_name"
if [[ $private_package == "1" ]]; then
  helper_cdhash=$(codesign -d --verbose=4 "$resources/$helper_executable_name" 2>&1 | awk -F= '$1 == "CDHash" {print $2}')
  [[ $helper_cdhash =~ ^[0-9a-f]{40}$ ]] || fail "missing helper cdhash"
  helper_requirement="identifier \"$helper_identifier\" and cdhash H\"$helper_cdhash\""
  chmod 0644 "$resources/Release/release.json"
  plutil -replace helper_code_signing_requirement -string "$helper_requirement" "$resources/Release/release.json"
  chmod 0444 "$resources/Release/release.json"
fi
codesign --force --sign "$signing_identity" \
  "${timestamp_arguments[@]}" \
  --options runtime \
  "$assembled_app"

codesign --verify --deep --strict --verbose=2 "$assembled_app"
codesign --verify --strict \
  -R="$helper_requirement" \
  "$resources/$helper_executable_name"
if [[ $private_package == "1" ]]; then
  if codesign --verify --strict -R="$client_requirement" "$assembled_app" 2>/dev/null; then
    fail "embedded private daemon unexpectedly admits the app"
  fi
  app_cdhash=$(codesign -d --verbose=4 "$assembled_app" 2>&1 | awk -F= '$1 == "CDHash" {print $2}')
  [[ $app_cdhash =~ ^[0-9a-f]{40}$ ]] || fail "missing app cdhash"
  client_requirement="identifier \"$app_identifier\" and cdhash H\"$app_cdhash\""
fi
codesign --verify --strict -R="$client_requirement" "$assembled_app"

mv "$assembled_app" "$final_app"
echo "$final_app"
