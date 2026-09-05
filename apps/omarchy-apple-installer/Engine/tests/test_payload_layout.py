"""Payload tree members must be extractable in archive order."""
import io
from pathlib import Path, PurePosixPath
import stat
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cleanroom'))
from build_payload import write_directories


class PayloadLayoutTests(unittest.TestCase):
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
