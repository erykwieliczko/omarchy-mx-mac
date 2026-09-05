# SPDX-License-Identifier: MIT
"""Seal a verified disposable J713 image set into an offline OS package."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile

from boot_inputs import load_profile, stub_members


def write_directories(archive, names):
    # The engine extracts in ZIP order and creates directories from their
    # records. Include every parent before any of its files are written.
    directories = {parent.as_posix() + '/' for name in names
                   for parent in Path(name).parents if parent != Path('.')}
    for name in sorted(directories, key=lambda name: (name.count('/'), name)):
        info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 0, 0, 0))
        info.create_system = 3
        info.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
        archive.writestr(info, b'')


def descriptor(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('unsafe payload input: ' + str(path))
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        while block := reader.read(4 * 1024 * 1024):
            digest.update(block)
    return {'size_bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def verified_descriptors(images, boot, verification):
    if (verification / 'result').read_text() != 'passed\n':
        raise ValueError('root verification did not pass')
    records = {}
    for directory, receipt_name, names in (
        (images, verification / 'verification.json', ('root.img', 'boot.img', 'initramfs.img')),
        (boot, boot / 'receipt.json', ('grub.cfg', 'BOOTAA64.EFI', 'boot.bin')),
    ):
        expected = json.loads(receipt_name.read_text())
        for name in names:
            record = descriptor(directory / name)
            if record != expected.get(name):
                raise ValueError('verified input changed: ' + name)
            records[directory / name] = record
    return records


def build(inputs, images, boot, verification, destination):
    verified = verified_descriptors(images, boot, verification)
    profile = load_profile(Path(__file__).parent / 'profiles/j713.json')
    with zipfile.ZipFile(inputs / 'apple-restore.zip') as archive:
        stub_members(archive, profile)
    files = {
        'root.img': images / 'root.img',
        'boot.img': images / 'boot.img',
        'omarchy-volume.icns': images / 'omarchy-volume.icns',
        'esp/m1n1/boot.bin': boot / 'boot.bin',
        'esp/EFI/BOOT/BOOTAA64.EFI': boot / 'BOOTAA64.EFI',
        'apple-restore.zip': inputs / 'apple-restore.zip',
    }
    if files['root.img'].stat().st_size != 34359738368 or files['boot.img'].stat().st_size != 2147483648:
        raise ValueError('unexpected partition image sizes')
    receipt = {'schema_version': 1, 'profile': profile, 'files': {}}
    # The ZIP is never exposed under its final name until streaming and CRC
    # verification finish; failed candidates remain distinguishable.
    pending = destination.with_name(destination.name + '.pending')
    if destination.exists() or destination.is_symlink():
        raise ValueError('payload destination already exists')
    with zipfile.ZipFile(pending, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
        write_directories(archive, files)
        for name, path in files.items():
            if path.is_symlink() or not path.is_file():
                raise ValueError('unsafe payload member: ' + name)
            record = verified[path] if path in verified else descriptor(path)
            receipt['files'][name] = record
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info._compresslevel = 3
            info.file_size = record['size_bytes']
            digest = hashlib.sha256()
            with path.open('rb') as reader, archive.open(info, 'w', force_zip64=True) as writer:
                while block := reader.read(4 * 1024 * 1024):
                    digest.update(block)
                    writer.write(block)
            if digest.hexdigest() != record['sha256']:
                raise ValueError('payload input changed while sealing: ' + name)
            print('sealed ' + name, flush=True)
    with zipfile.ZipFile(pending) as archive:
        if archive.testzip() is not None:
            raise ValueError('payload ZIP CRC verification failed')
    pending.rename(destination)
    receipt['payload'] = descriptor(destination)
    destination.with_suffix('.receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'images', 'boot', 'verification', 'destination'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(**vars(args)), indent=2))
