# Neo Wi-Fi policy conversion

Source: linux-enablement-mac-alpha, commit
`6b4eb9858dc5841e3a848cd21b727ab32d7f44e6`, tools/j700_wifi_*.py.
The original repository license is preserved in COPYING. Local changes make
sibling imports package-relative. These files contain conversion code, not
Apple firmware or precomputed policy data. Inputs are downloaded from Apple
and calibration is collected from the target Mac during installation.

The installer also memoizes the four read-only parsed policy tables per worker
to avoid reparsing the entire Apple country database for each output. Outputs
remain independently pinned; no generated policy data is distributed.

World-policy packaging is updated from published commit
`6b4eb9858dc5841e3a848cd21b727ab32d7f44e6` (j700_wifi_boot_policy.py).
It preserves the original-derived XZ package as `policy/world-XZ.bin`, records
Linux country `00` separately from firmware profile `XZ`, and never substitutes
XZ for an unavailable explicitly selected country. The shared kernel requires
published world/passive-scan commit `6ba27a80f991f1627f41fa71aa241d600246e948`.
