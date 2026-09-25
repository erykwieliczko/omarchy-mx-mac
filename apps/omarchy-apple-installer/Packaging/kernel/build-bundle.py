# SPDX-License-Identifier: MIT
"""Publishable Neo kernel/boot bundle with complete corresponding build sources."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
VERSION = '2026.09.25.2'
PREFIX = 'https://f005.backblazeb2.com/file/omarchymacexperimental/aurora/boot/' + VERSION + '/'
M1N1 = 'b6c71c8d9840f6686677aee8475862948d0520df'
LINUX = '783d4c8ee57b9895ef140c47ce3ef8ee86d1ca17'
UBOOT = 'b0f6d36e1c3ff4b4eceec663ded023830c237a30'


def record(path):
    with path.open('rb') as reader:
        return {'size_bytes': path.stat().st_size, 'sha256': hashlib.file_digest(reader, 'sha256').hexdigest()}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def compress(tree, output):
    plain = output.with_suffix('')
    with tarfile.open(plain, 'w', format=tarfile.GNU_FORMAT) as writer:
        for path in sorted(tree.rglob('*')):
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError('source symlink')
            info = tarfile.TarInfo(path.relative_to(tree).as_posix())
            info.size, info.mode = path.stat().st_size, 0o644
            with path.open('rb') as reader:
                writer.addfile(info, reader)
    subprocess.run(['zstd', '-q', '-10', '-T4', str(plain), '-o', str(output)], check=True)
    plain.unlink()
    return {'url': PREFIX + output.name, **record(output)}


def build(args):
    args.output.mkdir(parents=True, exist_ok=False)
    tree, sources = args.output / 'bundle', args.output / 'sources'
    tree.mkdir(); sources.mkdir()
    old = args.previous / 'aurora-silicon-boot-j700-2026.09.25.1.tar.zst'
    if record(old)['sha256'] != '3902954284627e89e33f42d66b06a6a30dda0926fb7903d4f3191b786a7ba5f4':
        raise ValueError('original boot bundle changed')
    plain = subprocess.check_output(['zstd', '-dc', str(old)])
    with tarfile.open(fileobj=io.BytesIO(plain)) as reader:
        manifest = json.load(reader.extractfile('manifest.json'))
        for entry in reader:
            if not entry.isfile() or entry.name == 'manifest.json':
                continue
            path = tree / entry.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(reader.extractfile(entry).read())
    old_sources = args.previous / 'aurora-silicon-boot-sources-2026.09.25.1.tar.zst'
    if record(old_sources)['sha256'] != 'dc8e8546c29f57168d70646b8142615da8a6eab09aec91133bac69e1619ff1f1':
        raise ValueError('original boot source archive changed')
    shutil.copy2(old_sources, sources / old_sources.name)
    for name, repository, revision in (('linux', args.linux, LINUX), ('m1n1-stage2', args.m1n1, M1N1), ('u-boot', args.uboot, UBOOT)):
        actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError('source pin differs: ' + name)
        subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar', '-o', str(sources / (name + '.tar')), revision], check=True)
    shutil.copytree(HERE, sources / 'recipes/kernel', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(args.artifacts / 'config', sources / 'kernel.config')
    shutil.copy2(args.artifacts / 'build-receipt.json', sources / 'kernel-build-receipt.json')
    (sources / 'README.txt').write_text(
        'Aurora Silicon Neo kernel bundle ' + VERSION + '\n'
        'Linux branch J700 at ' + LINUX + '; configuration and complete source included.\n'
        'Build: recipes/kernel/build-kernel.sh SOURCE WORKSPACE RUST_TOOLCHAIN (LLVM 22, Rust 1.95).\n'
        'Initial UKI: build-payload.py uses a disposable extraction of the pinned Omarchy OS ZIP\n'
        'with its boot.img contents restored under /boot, ARM64 QEMU binfmt and bubblewrap.\n'
        'See recipes/kernel/README.md for the complete packaging procedure and validation limits.\n'
        'Stage2 m1n1 at ' + M1N1 + ': extract m1n1-stage2.tar, restore artwork from the nested\n'
        '2026.09.25.1 sources into artwork/, then make RELEASE=1 CHAINLOADING=1 T8140_KIS_PROXY=1 USE_CLANG=1 BUILDSTD=1.\n'
        'Persistent stage1 is unchanged; its complete sources, configs,\n'
        'artwork, recipes and license notices are in the nested 2026.09.25.1 source archive.\n'
        'U-Boot J700 ' + UBOOT + ': recipes/kernel/build-uboot.sh SOURCE OUTPUT, GCC aarch64 cross compiler.\n'
        'The full main t8140-j700.dtb is compiled with this Linux kernel. No radio DT with fixed MACs is used.\n')
    source_pin = compress(sources, args.output / ('aurora-silicon-kernel-sources-' + VERSION + '.tar.zst'))

    uboot = (args.uboot_build / 'u-boot.bin').read_bytes()
    shutil.copy2(args.uboot_build / '.config', tree / 'build/u-boot.config')
    (tree / 'build/u-boot-receipt.json').write_bytes(canonical({'source_revision': UBOOT, 'boot_mode': 'uuid-bound-esp', 'artifacts': {'u-boot.bin': record(args.uboot_build / 'u-boot.bin'), 'u-boot.config': record(args.uboot_build / '.config')}}))
    size = struct.unpack_from('<Q', uboot, 16)[0]
    if uboot[56:60] != b'ARM\x64' or not len(uboot) <= size <= 64 * 1024**2:
        raise ValueError('invalid U-Boot image')
    dtb = (args.artifacts / 't8140-j700.dtb').read_bytes()
    if dtb[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', dtb, 4)[0] != len(dtb):
        raise ValueError('invalid kernel device tree')
    stage2 = (args.m1n1 / 'build/m1n1.bin').read_bytes()
    (tree / 'boot/boot.bin').write_bytes(stage2 + dtb + gzip.compress(uboot.ljust(size, b'\0'), mtime=0))
    for directory in ('kernel', 'build', 'licenses/linux', 'licenses/m1n1-stage2'):
        (tree / directory).mkdir(parents=True, exist_ok=True)
    for name in ('kernel.efi', 'root.tar.zst'):
        shutil.copy2(args.payload / name, tree / 'kernel' / name)
    shutil.copy2(args.artifacts / 'build-receipt.json', tree / 'build/kernel-receipt.json')
    shutil.copy2(args.payload / 'payload-receipt.json', tree / 'build/payload-receipt.json')
    shutil.copy2(args.artifacts / 'config', tree / 'build/kernel.config')
    shutil.copy2(args.linux / 'COPYING', tree / 'licenses/linux/COPYING')
    shutil.copytree(args.linux / 'LICENSES', tree / 'licenses/linux/LICENSES')
    shutil.copy2(args.m1n1 / 'LICENSE', tree / 'licenses/m1n1-stage2/LICENSE')
    shutil.copytree(args.uboot / 'Licenses', tree / 'licenses/u-boot-current/Licenses')
    kernel = json.loads((args.payload / 'payload-receipt.json').read_text())
    manifest.update(version=VERSION, sources={**manifest['sources'], 'archive': source_pin,
                    'linux': {'repository': 'https://github.com/aurora-silicon/linux-aurora', 'branch': 'J700', 'revision': LINUX},
                    'u_boot': {'repository': 'https://github.com/aurora-silicon/u-boot', 'branch': 'J700', 'revision': UBOOT},
                    'device_trees': {'j700_linux_revision': LINUX},
                    'm1n1_stage2': {'repository': 'https://github.com/aurora-silicon/m1n1-aurora', 'branch': 'J700', 'revision': M1N1}},
                    kernel={'release': kernel['kernel_release'], 'menu_name': kernel['menu_name'],
                            'uki': 'kernel/kernel.efi', 'root': 'kernel/root.tar.zst',
                            'physical_boot_verified': False},
                    files={path.relative_to(tree).as_posix(): record(path) for path in sorted(tree.rglob('*')) if path.is_file()})
    (tree / 'manifest.json').write_bytes(canonical(manifest))
    pin = {'schema_version': 1, **compress(tree, args.output / ('aurora-silicon-boot-j700-' + VERSION + '.tar.zst')),
           'manifest_sha256': record(tree / 'manifest.json')['sha256']}
    (args.output / 'bundle.json').write_text(json.dumps(pin, indent=2) + '\n')
    (args.output / 'sources.json').write_text(json.dumps(source_pin, indent=2) + '\n')
    print(json.dumps(pin, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifacts', 'payload', 'previous', 'linux', 'm1n1', 'uboot', 'uboot_build', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).resolve(), required=True)
    build(parser.parse_args())
