# Neo's radio requires an explicit country policy before scanning. Keep the
# choice in iwd, the installed NetworkManager backend, across normal reboots.
omarchy_wifi_country_required() {
  [[ -f /sys/firmware/devicetree/base/compatible ]] &&
    tr '\0' '\n' </sys/firmware/devicetree/base/compatible | grep -qx 'apple,j700'
}

omarchy_wifi_country_valid() {
  [[ $1 =~ ^[A-Z]{2}$ ]] &&
    awk -F '\t' -v country="$1" '$1 == country { found=1 } END { exit !found }' \
      /usr/share/zoneinfo/iso3166.tab
}

omarchy_prompt_wifi_country() {
  omarchy_wifi_country_required || return 0
  local choice status
  while true; do
    choice=$(awk -F '\t' '!/^#/ && NF >= 2 { print $1 "  " $2 }' \
      /usr/share/zoneinfo/iso3166.tab |
      gum filter --height 10 --placeholder "Search country" \
        --header "Select the country you are physically in (Wi-Fi regulations)") && status=0 || status=$?
    (( status == 0 )) || return "$status"
    wifi_country=${choice%% *}
    omarchy_wifi_country_valid "$wifi_country" || continue
    if [[ -f /usr/lib/firmware/mediatek/mt7932/policy/$wifi_country.bin ]]; then
      return 0
    fi
    if gum confirm "Wi-Fi policy for $wifi_country is unavailable. Continue with Ethernet/offline?"; then
      return 0
    else
      status=$?
      (( status == 130 )) && return "$status"
    fi
  done
}

# Replacing only Country preserves other iwd settings, comments and sections.
# Treat repeated General sections/keys as one setting, then replace atomically.
omarchy_write_iwd_country() {
  local country=$1 config=$2 temporary source_file=/dev/null
  omarchy_wifi_country_valid "$country" || return 1
  install -d -m0755 "${config%/*}"
  [[ ! -e $config ]] || source_file=$config
  temporary=$(mktemp "${config}.XXXXXX") || return 1
  if ! awk -v country="$country" '
    /^[[:space:]]*\[/ {
      general = ($0 ~ /^[[:space:]]*\[General\][[:space:]]*$/)
      print
      if (general && !written) { print "Country=" country; written=1 }
      next
    }
    general && /^[[:space:]]*Country[[:space:]]*=/ { next }
    { print }
    END { if (!written) print "\n[General]\nCountry=" country }
  ' "$source_file" >"$temporary"; then
    rm -f "$temporary"
    return 1
  fi
  if [[ -e $config ]]; then
    chmod --reference="$config" "$temporary" || { rm -f "$temporary"; return 1; }
    chown --reference="$config" "$temporary" || { rm -f "$temporary"; return 1; }
  else
    chmod 0644 "$temporary" || { rm -f "$temporary"; return 1; }
  fi
  mv -f "$temporary" "$config"
}

omarchy_configure_wifi_country() {
  [[ -n ${wifi_country:-} ]] || return 0
  install -d -m0755 /etc/iwd
  if [[ ! -e /etc/iwd/main.conf ]]; then
    install -m0644 /dev/null /etc/iwd/main.conf
  fi
  omarchy_write_iwd_country "$wifi_country" /etc/iwd/main.conf || return 1
  # The shared regulatory service must agree with the user's explicit answer.
  install -d -m0755 /etc/conf.d
  local regdom=/etc/conf.d/wireless-regdom temporary
  temporary=$(mktemp "${regdom}.XXXXXX") || return 1
  if [[ -f $regdom ]]; then
    sed '/^[[:space:]]*WIRELESS_REGDOM=/d' "$regdom" >"$temporary"
  fi
  printf 'WIRELESS_REGDOM="%s"\n' "$wifi_country" >>"$temporary"
  chmod 0644 "$temporary"
  mv -f "$temporary" "$regdom"
  if systemctl is-active --quiet iwd.service; then
    systemctl restart iwd.service || echo "Wi-Fi country saved; iwd restart failed. See journalctl -u iwd."
  fi
  iw reg set "$wifi_country" || echo "Wi-Fi country saved; runtime hint failed. Check Wi-Fi after reboot."
  iw reg get || true
}
