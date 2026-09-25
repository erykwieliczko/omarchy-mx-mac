# SPDX-License-Identifier: MIT
"""Build the Neo /aurora payload and initial UKI in a disposable original OS root."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
NAME = 'aurora-silicon-dirtyroom-J700'


def sha(path):
    with path.open('rb') as reader:
        return hashlib.file_digest(reader, 'sha256').hexdigest()


def archive(tree, output):
    plain = output.with_suffix('')
    with tarfile.open(plain, 'w', format=tarfile.GNU_FORMAT) as writer:
        for path in sorted(tree.rglob('*')):
            if path.is_symlink():
                raise ValueError('payload symlink: ' + str(path))
            if not path.is_file():
                continue
            info = tarfile.TarInfo(path.relative_to(tree).as_posix())
            info.size = path.stat().st_size
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            with path.open('rb') as reader:
                writer.addfile(info, reader)
    subprocess.run(['zstd', '-q', '-10', '-T4', str(plain), '-o', str(output)], check=True)
    plain.unlink()


def build(args):
    receipt = json.loads((args.artifacts / 'build-receipt.json').read_text())
    for name, record in receipt['files'].items():
        path = args.artifacts / name
        if path.stat().st_size != record['size_bytes'] or sha(path) != record['sha256']:
            raise ValueError('kernel artifact changed: ' + name)
    release = receipt['kernel_release']
    args.output.mkdir(parents=True, exist_ok=False)
    store = args.output / 'store'
    store.mkdir()
    for name in ('Image', 'config', 'kernel.release', 'build-receipt.json'):
        shutil.copy2(args.artifacts / name, store / name)
    shutil.copytree(args.artifacts / 'lib/modules' / release, store / 'modules')
    runtime = store / 'runtime'
    runtime.mkdir()
    for name in ('boot-update', 'aurora-kernel.conf', 'aurora-j700-radio.service'):
        shutil.copy2(HERE / 'runtime' / name, runtime / name)
    (runtime / 'boot-update').chmod(0o755)
    inventory = ''.join(sha(path) + '  ' + path.relative_to(store).as_posix() + '\n'
                        for path in sorted(store.rglob('*')) if path.is_file())
    (store / 'files.sha256').write_text(inventory)
    payload = args.output / 'root.tar.zst'
    archive(store, payload)

    # No host files or host modules enter the ARM image. The build root is a
    # disposable extraction of the exact original OS package, not a test Mac.
    root_store = args.root / 'aurora/build'
    shutil.copytree(store, root_store)
    (root_store / 'modules/vmlinuz').symlink_to('../Image')
    (args.root / 'usr/lib/modules' / release).symlink_to('/aurora/build/modules')
    for source, target, mode in (
        ('aurora-kernel.conf', 'etc/mkinitcpio.conf.d/96-aurora-kernel.conf', 0o644),
        ('aurora-bootstrap.service', 'usr/lib/systemd/system/aurora-bootstrap.service', 0o644),
        ('aurora-bootstrap.install', 'etc/initcpio/install/aurora-bootstrap', 0o644),
        ('switch-root.conf', 'usr/lib/aurora/switch-root.conf', 0o644),
    ):
        destination = args.root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / 'runtime' / source, destination)
        destination.chmod(mode)
    bootstrap = (HERE / 'runtime/bootstrap').read_text().replace('@KERNEL_RELEASE@', release).replace('@PAYLOAD_SHA256@', sha(payload))
    destination = args.root / 'usr/lib/aurora/bootstrap'
    destination.write_text(bootstrap)
    destination.chmod(0o755)
    (args.root / 'etc/mkinitcpio.conf.d/99-aurora-bootstrap.conf').write_text('HOOKS+=(aurora-bootstrap)\n')
    # The initial image is plaintext; the first-boot encrypt hook changes this
    # when selected, then bootstrap regenerates the UKI with the resulting UUID.
    menu = (args.upstream / 'esp/limine.conf').read_text()
    cmdline = next(line.strip().removeprefix('cmdline: ').strip() for line in menu.splitlines()
                   if line.strip().startswith('cmdline:'))
    cmdline += ' idle=nop arm64.nowfxt firmware_class.path=/vendorfw'
    (args.root / 'aurora/initial.cmdline').write_text(cmdline + '\n')
    # Remove inherited host ACL entries whose UIDs cannot be mapped in the namespace.
    subprocess.run(['setfacl', '-Rb', str(args.root)], check=True)
    subprocess.run(['find', str(args.root), '-type', 'd', '-exec', 'setfacl', '-k', '{}', '+'], check=True)
    subprocess.run(['bwrap', '--unshare-user', '--uid', '0', '--gid', '0',
                    '--bind', str(args.root), '/', '--dev', '/dev', '--proc', '/proc',
                    '--ro-bind', '/sys', '/sys', '/usr/bin/mkinitcpio',
                    '--kernel', release, '--generate', '/aurora/initial.initramfs',
                    '--uki', '/aurora/initial.efi', '--cmdline', '/aurora/initial.cmdline',
                    '--skiphooks', 'autodetect,kms', '--nopost'], check=True)
    for source, destination in (('initial.efi', 'kernel.efi'), ('initial.initramfs', 'initramfs.img'),
                                ('initial.cmdline', 'cmdline')):
        shutil.copy2(args.root / 'aurora' / source, args.output / destination)
    result = {'kernel_release': release, 'menu_name': NAME, 'root_payload_sha256': sha(payload),
              'uki_sha256': sha(args.output / 'kernel.efi'),
              'source_revision': receipt['source_revision'], 'physical_boot_verified': False}
    (args.output / 'payload-receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifacts', 'root', 'upstream', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).resolve(), required=True)
    build(parser.parse_args())
