#!/bin/bash
set -euo pipefail
source_directory=$(realpath "$1")
output_directory=$(realpath -m "$2")
revision=b0f6d36e1c3ff4b4eceec663ded023830c237a30
[[ $(git -C "$source_directory" rev-parse HEAD) == "$revision" ]]
[[ -z $(git -C "$source_directory" status --porcelain) ]]
mkdir -p "$output_directory"
export SOURCE_DATE_EPOCH=$(git -C "$source_directory" show -s --format=%ct HEAD)
build=(make -C "$source_directory" "O=$output_directory" CROSS_COMPILE=aarch64-linux-gnu- "-j${OMARCHY_BUILD_JOBS:-10}")
"${build[@]}" apple_m1_defconfig
python3 - "$output_directory/.config" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
values = {
    'BOOTCOMMAND': '"if fdt addr ${fdtcontroladdr} && fdt get value omarchy_esp /chosen asahi,efi-system-partition && nvme scan && part number nvme 0 ${omarchy_esp} omarchy_part && fatsize nvme 0:${omarchy_part} /EFI/BOOT/BOOTAA64.EFI && itest ${filesize} -le 0x4000000 && fatload nvme 0:${omarchy_part} ${loadaddr} /EFI/BOOT/BOOTAA64.EFI; then bootefi ${loadaddr} ${fdtcontroladdr}; else echo Aurora disk boot failed; fi"',
    'SYS_CBSIZE': '1024', 'SYS_PBSIZE': '1044', 'BOOTDELAY': '0',
}
for name in ('USE_BOOTCOMMAND', 'EFI_LOADER', 'CMD_BOOTEFI_BINARY', 'CMD_FDT', 'CMD_NVME',
             'CMD_PART', 'PARTITION_UUIDS', 'CMD_FAT', 'FS_FAT', 'CMD_ITEST',
             'ENV_IS_NOWHERE', 'NVME_APPLE', 'APPLE_DOCKCHANNEL_SERIAL', 'VIDEO_SIMPLE'):
    values[name] = 'y'
for name in ('USE_PREBOOT', 'PREBOOT_DEFINED', 'APPLE_MTP_KEYB', 'APPLE_SPI_KEYB', 'USB',
             'USB_KEYBOARD', 'USB_XHCI_DWC3', 'USB_DWC3', 'USB_STORAGE', 'APPLE_PRELOADED_EFI'):
    values[name] = 'n'
lines = [line for line in path.read_text().splitlines()
         if not any(line.startswith('CONFIG_' + name + '=') or line == '# CONFIG_' + name + ' is not set' for name in values)]
lines += ['# CONFIG_' + name + ' is not set' if value == 'n' else 'CONFIG_' + name + '=' + value for name, value in values.items()]
path.write_text('\n'.join(lines) + '\n')
PY
"${build[@]}" olddefconfig
python3 - "$output_directory/.config" <<'PYCONFIG'
from pathlib import Path
import sys
config = Path(sys.argv[1]).read_text()
for name in ('USE_PREBOOT', 'APPLE_MTP_KEYB', 'APPLE_SPI_KEYB', 'USB_KEYBOARD', 'USB_XHCI_DWC3'):
    if 'CONFIG_' + name + '=y' in config:
        raise SystemExit('Unsafe U-Boot peripheral autoprobe: ' + name)
for name in ('EFI_LOADER', 'CMD_BOOTEFI_BINARY', 'CMD_NVME', 'CMD_FDT', 'CMD_PART', 'PARTITION_UUIDS', 'FS_FAT', 'NVME_APPLE'):
    if 'CONFIG_' + name + '=y' not in config:
        raise SystemExit('Missing disk EFI loader feature: ' + name)
PYCONFIG
"${build[@]}"
