#!/bin/bash
set -euo pipefail

source_directory=$(realpath "$1")
output_directory=$(realpath -m "$2")
rust_toolchain=$(realpath "$3")
recipe_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
revision=783d4c8ee57b9895ef140c47ce3ef8ee86d1ca17
[[ $(git -C "$source_directory" rev-parse HEAD) == "$revision" ]]
[[ -z $(git -C "$source_directory" status --porcelain) ]]
mkdir -p "$output_directory/build" "$output_directory/artifacts"
exec 9>"$output_directory/build.lock"
flock -n 9
export KBUILD_BUILD_USER=aurora KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP=$(git -C "$source_directory" show -s --format=%cD HEAD)
build=(make -C "$source_directory" "O=$output_directory/build" ARCH=arm64 LLVM=1
  "RUSTC=$rust_toolchain/bin/rustc" LOCALVERSION= "-j${OMARCHY_BUILD_JOBS:-10}")
bash "$source_directory/scripts/kconfig/merge_config.sh" -m -O "$output_directory/build" \
  "$source_directory/arch/arm64/configs/j700_dirtyroom_gpu_defconfig" \
  "$recipe_directory/j700-distribution.config"
"${build[@]}" olddefconfig
python3 - "$output_directory/build/.config" "$recipe_directory/j700-distribution.config" <<'PY'
import sys
from pathlib import Path
actual = dict(line.split('=', 1) for line in Path(sys.argv[1]).read_text().splitlines()
              if line.startswith('CONFIG_'))
for line in Path(sys.argv[2]).read_text().splitlines():
    if line.startswith('CONFIG_'):
        key, value = line.split('=', 1)
        got = actual.get(key, 'n')
        if got != value and not (value == 'm' and got == 'y'):
            raise SystemExit(f'Required kernel option {key}: requested {value}, got {got}')
    elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
        key = line.split()[1]
        if actual.get(key, 'n') != 'n':
            raise SystemExit(f'Forbidden kernel option {key} enabled')
PY
"${build[@]}" Image dtbs modules
release=$(cat "$output_directory/build/include/config/kernel.release")
[[ $release == "7.1.6-aurora-silicon-dirtyroom-j700.1" ]]
"${build[@]}" modules_install "INSTALL_MOD_PATH=$output_directory/artifacts" INSTALL_MOD_STRIP=1 DEPMOD=true
rm -f "$output_directory/artifacts/lib/modules/$release/build" "$output_directory/artifacts/lib/modules/$release/source"
depmod -b "$output_directory/artifacts" "$release"
install -m 0644 "$output_directory/build/arch/arm64/boot/Image" "$output_directory/artifacts/Image"
install -m 0644 "$output_directory/build/arch/arm64/boot/dts/apple/t8140-j700.dtb" "$output_directory/artifacts/t8140-j700.dtb"
install -m 0644 "$output_directory/build/.config" "$output_directory/artifacts/config"
install -m 0644 "$output_directory/build/System.map" "$output_directory/artifacts/System.map"
printf '%s\n' "$release" >"$output_directory/artifacts/kernel.release"
python3 "$recipe_directory/verify-kernel.py" "$source_directory" "$output_directory" "$rust_toolchain"
