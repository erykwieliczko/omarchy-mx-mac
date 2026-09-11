#!/bin/bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"
source "$ROOT/install/provisioning/wifi-country.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
config="$work/iwd/main.conf"
omarchy_write_iwd_country PL "$config"
grep -qx 'Country=PL' "$config"
cat >"$config" <<'CONFIG'
# Keep comments
[General]
EnableNetworkConfiguration=false
Country=US
[Network]
EnableIPv6=true
[General]
Country=DE
CONFIG
chmod 0600 "$config"
omarchy_write_iwd_country PL "$config"
[[ $(grep -c '^Country=' "$config") == "1" ]]
grep -qx 'Country=PL' "$config"
grep -qx 'EnableNetworkConfiguration=false' "$config"
grep -qx 'EnableIPv6=true' "$config"
grep -qx '# Keep comments' "$config"
[[ $(stat -c %a "$config") == "600" ]]
cp "$config" "$work/expected"
omarchy_write_iwd_country PL "$config"
cmp "$config" "$work/expected"
for invalid in 00 ZZ pl 'PL/../../x' ''; do
  if omarchy_write_iwd_country "$invalid" "$config"; then
    echo "Accepted invalid country: $invalid" >&2
    exit 1
  fi
  cmp "$config" "$work/expected"
done
# World/unset startup neither prompts nor overwrites intentional settings.
(
  unset wifi_country
  install() { echo 'Unexpected country write' >&2; return 99; }
  systemctl() { echo 'Unexpected network restart' >&2; return 99; }
  iw() { echo 'Unexpected country hint' >&2; return 99; }
  omarchy_configure_wifi_country
)
# First-run account setup must not require a country to discover Wi-Fi.
! grep -q 'omarchy_prompt_wifi_country' "$ROOT/bin/omarchy-provision-owner"
echo 'Wi-Fi country tests passed'
