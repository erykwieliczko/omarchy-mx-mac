#!/bin/bash
# SPDX-License-Identifier: MIT

set -euo pipefail

vgid="##VGID##"
product="##PRODUCT##"
esp="##ESP##"
stage1_sha256="##STAGE1_SHA256##"
stage1_size="##STAGE1_SIZE##"
recovery_free_kib="##RECOVERY_FREE_KIB##"

fail() {
  echo "Omarchy: $*" >&2
  exit 1
}

(( EUID == 0 )) || fail "Open this installer from the paired Recovery terminal."
[[ $(sysctl -n hw.model) == "$product" ]] || fail "This installation belongs to another Mac model."
resources=$(cd -- "$(dirname -- "$0")" && pwd -P)
system=$(cd "$resources/../../.." && pwd -P)
check_boot_space() {
  local free_kib
  free_kib=$(/bin/df -Pk "$system" | awk 'NR == 2 {print $4}')
  [[ $free_kib =~ ^[0-9]+$ ]] || fail "Cannot read free space in the Apple boot container."
  (( free_kib >= recovery_free_kib )) \
    || fail "Apple boot container has only $(( free_kib / 1024 )) MiB free; at least $(( recovery_free_kib / 1024 )) MiB is required. Reinstall using the updated installer."
}
image="$resources/boot.bin"
[[ -f $image && ! -L $image ]] || fail "The stage-1 image is missing."
actual_size=$(stat -f %z "$image")
[[ $actual_size == "$stage1_size" ]] || fail "The stage-1 size changed."
(( actual_size % 16384 == 0 )) || fail "The stage-1 image is not aligned."
"$resources/omarchy-restore-image" verify-stage1 "$image" "$stage1_sha256" "$stage1_size" \
  || fail "The stage-1 image changed."
actual_group=$(diskutil info -plist "$system" | plutil -extract APFSVolumeGroupID raw -o - -)
[[ $actual_group == "$vgid" ]] || fail "The mounted system volume changed."
actual_esp=$(diskutil info -plist "$esp" | plutil -extract DiskUUID raw -o - -)
[[ $(printf '%s' "$actual_esp" | tr '[:lower:]' '[:upper:]') == "$esp" ]] || fail "The EFI partition changed."
esp_type=$(diskutil info -plist "$esp" | plutil -extract Content raw -o - -)
[[ $esp_type == "EFI" ]] || fail "The boot partition is not EFI."

policy=$(bputil -d -v "$vgid")
[[ $policy == *": Paired"* && $policy == *"one true recoveryOS"* ]] \
  || fail "Shut down, hold the power button continuously, then select this Omarchy installation."

echo "Omarchy is ready to install its boot loader."
echo "Apple's security tools will ask you to authenticate."
echo "Press Return to continue, or Control-C to stop."
read -r reply
check_boot_space
echo "Updating Apple boot policy..."
bputil -nc -v "$vgid"
check_boot_space
echo "Configuring the Omarchy boot loader..."
kmutil configure-boot -c "$image" --raw --entry-point 2048 --lowest-virtual-address 0 -v "$system"
mount -u -w "$system"
if [[ -e $system/.IAPhysicalMedia ]]; then
  mv "$system/.IAPhysicalMedia" "$system/IAPhysicalMedia-disabled.plist"
fi
version="$system/System/Library/CoreServices/SystemVersion"
if [[ -e $version-disabled.plist ]]; then
  mv -f "$version-disabled.plist" "$version.plist"
fi
bless --mount "$system" --setBoot
sync
echo "The boot loader is installed. Reboot when you are ready."
