# SPDX-License-Identifier: MIT
"""Verify the complete compiled/staged module closure and record exact kernel inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def capture(*arguments):
    return subprocess.check_output(list(map(str, arguments)), text=True).strip()


def digest(path):
    with Path(path).open('rb') as reader:
        return hashlib.file_digest(reader, 'sha256').hexdigest()


def verify(source, workspace, rust):
    build, artifacts = workspace / 'build', workspace / 'artifacts'
    release = (artifacts / 'kernel.release').read_text().strip()
    if release != '7.1.6-aurora-silicon-dirtyroom-j700.1':
        raise ValueError('unexpected kernel release')
    tree = artifacts / 'lib/modules' / release
    expected = {'kernel/' + line.removesuffix('.o').removesuffix('.ko') + '.ko.zst'
                for line in (build / 'modules.order').read_text().splitlines()}
    actual = {path.relative_to(tree).as_posix() for path in tree.rglob('*.ko.zst')}
    if not expected or actual != expected:
        raise ValueError('staged kernel modules differ from modules.order')
    for path in sorted(actual):
        if capture('modinfo', '-F', 'vermagic', tree / path).split()[0] != release:
            raise ValueError('kernel/module version mismatch: ' + path)
    diagnostics = subprocess.run(['depmod', '-b', str(artifacts), '-e', '-F',
                                  str(artifacts / 'System.map'), release],
                                 capture_output=True, text=True, check=True)
    if diagnostics.stderr.strip():
        raise ValueError('unresolved kernel module symbols: ' + diagnostics.stderr)
    image = (artifacts / 'Image').read_bytes()
    if image[:2] != b'MZ' or image[56:60] != b'ARM\x64':
        raise ValueError('kernel is not an EFI-capable ARM64 Image')
    if 'apple,j700' not in capture('fdtget', artifacts / 't8140-j700.dtb', '/', 'compatible').split():
        raise ValueError('wrong kernel device tree')
    receipt = {
        'schema_version': 1, 'menu_name': 'aurora-silicon-dirtyroom-J700',
        'source_repository': 'https://github.com/aurora-silicon/linux-aurora',
        'source_branch': 'J700', 'source_revision': capture('git', '-C', source, 'rev-parse', 'HEAD'),
        'kernel_release': release, 'module_count': len(actual),
        'config_sha256': digest(artifacts / 'config'),
        'base_config_sha256': digest(source / 'arch/arm64/configs/j700_dirtyroom_gpu_defconfig'),
        'fragment_sha256': digest(Path(__file__).with_name('j700-distribution.config')),
        'toolchain': {'clang': capture('clang', '--version').splitlines()[0],
                      'lld': capture('ld.lld', '--version'),
                      'rustc': capture(rust / 'bin/rustc', '--version'),
                      'bindgen': capture('bindgen', '--version')},
        'physical_boot_verified': False,
        'files': {},
    }
    for path in sorted(artifacts.rglob('*')):
        if path.is_symlink():
            raise ValueError('unexpected staged artifact symlink: ' + str(path))
        if path.is_file() and path.name != 'build-receipt.json':
            receipt['files'][path.relative_to(artifacts).as_posix()] = {
                'size_bytes': path.stat().st_size, 'sha256': digest(path)}
    (artifacts / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Verified {release}: {len(actual)} matching modules; no unresolved symbols')


if __name__ == '__main__':
    verify(*(Path(value).resolve() for value in sys.argv[1:]))
