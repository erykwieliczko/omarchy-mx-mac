"""Payload tree members must be extractable in archive order."""
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cleanroom'))
from build_payload import descriptor, verified_descriptors, write_directories


class PayloadLayoutTests(unittest.TestCase):
    def test_stale_verification_cannot_seal_different_images_or_boot_files(self):
        for changed in ('root.img', 'boot.img', 'initramfs.img', 'j713/grub.cfg', 'j700/BOOTAA64.EFI', 'j713/boot.bin', 'j700/Image'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                images, boot, verification = [root / name for name in ('images', 'boot', 'verification')]
                for directory in (images, boot, verification):
                    directory.mkdir()
                (verification / 'result').write_text('passed\n')
                for directory, receipt, names in (
                    (images, verification / 'verification.json', ('root.img', 'boot.img', 'initramfs.img')),
                    (boot, boot / 'receipt.json', tuple(f'{model}/{name}' for model in ('j713', 'j700')
                                                     for name in ('grub.cfg', 'BOOTAA64.EFI', 'boot.bin', 'Image', 'initramfs.img'))),
                ):
                    for name in names:
                        (directory / name).parent.mkdir(parents=True, exist_ok=True)
                        (directory / name).write_bytes(b'verified bytes')
                    receipt.write_text(json.dumps({name: descriptor(directory / name) for name in names}))
                self.assertEqual(len(verified_descriptors(images, boot, verification)), 13)
                path = images / changed if (images / changed).exists() else boot / changed
                path.write_bytes(b'different bytes')
                with self.assertRaisesRegex(ValueError, 'verified input changed'):
                    verified_descriptors(images, boot, verification)

    def test_parent_directories_precede_boot_files(self):
        names = ['root.img', 'esp/m1n1/boot.bin', 'esp/EFI/BOOT/BOOTAA64.EFI']
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            write_directories(archive, names)
            for name in names:
                archive.writestr(name, b'fixture')
        with zipfile.ZipFile(output) as archive:
            seen = set()
            for item in archive.infolist():
                for parent in PurePosixPath(item.filename).parents:
                    if str(parent) != '.':
                        self.assertIn(str(parent) + '/', seen)
                self.assertNotIn(item.filename, seen)
                seen.add(item.filename)
                if item.is_dir():
                    self.assertEqual(item.file_size, 0)
                    self.assertTrue(stat.S_ISDIR(item.external_attr >> 16))
                    self.assertEqual((item.external_attr >> 16) & 0o777, 0o755)
            self.assertEqual(len(seen), len(names) + 4)

    def test_shared_parents_are_written_once(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            write_directories(archive, ['esp/m1n1/a', 'esp/m1n1/b'])
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ['esp/', 'esp/m1n1/'])
