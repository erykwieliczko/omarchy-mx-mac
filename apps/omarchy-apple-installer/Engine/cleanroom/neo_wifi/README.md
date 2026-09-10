# Neo Wi-Fi policy conversion

Source: linux-enablement-mac-alpha, commit
`eb18361654bef156f170f1a70530227757056933`, tools/j700_wifi_*.py.
The original repository license is preserved in COPYING. Local changes make
sibling imports package-relative. These files contain conversion code, not
Apple firmware or precomputed policy data. Inputs are downloaded from Apple
and calibration is collected from the target Mac during installation.

The installer also memoizes the four read-only parsed policy tables per worker
to avoid reparsing the entire Apple country database for each output. Outputs
remain independently pinned; no generated policy data is distributed.
