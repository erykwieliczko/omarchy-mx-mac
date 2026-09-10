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
omarchy_wifi_country_required() { return 1; }
gum() { echo 'Unexpected prompt for non-Neo' >&2; return 99; }
omarchy_prompt_wifi_country
omarchy_wifi_country_required() { return 0; }
for cancelled in 1 130; do
  gum() { cat >/dev/null; return "$cancelled"; }
  status=0
  omarchy_prompt_wifi_country || status=$?
  [[ $status == "$cancelled" ]]
done
# An unavailable policy never forces a made-up country; explicit offline
# continuation preserves the actual country for a future firmware update.
gum() {
  case $1 in
    filter) cat >/dev/null; echo 'PL  Poland' ;;
    confirm) return 0 ;;
  esac
}
omarchy_prompt_wifi_country
[[ $wifi_country == "PL" ]]
echo 'Wi-Fi country tests passed'
