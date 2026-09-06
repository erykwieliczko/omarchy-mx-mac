"""Exercise Apple download admission without network access or physical disks."""
import hashlib
import io
import plistlib
import json
import stat
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanroom"))
from apple_inputs import AppleRedirects, AppleWorkspace, apple_url, download_ipsw, mounted_system_image, retain_stub_inputs, verify_retained_workspace, load_apple_inputs
from boot_inputs import BootInputError, load_profile


URL = "https://updates.cdn-apple.com/build/Restore.ipsw"


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, length=None, encoding="identity", url=URL):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data) if length is None else length),
                        "Content-Encoding": encoding}
        self.url = url

    def geturl(self):
        return self.url


class AppleInputDownloadTests(unittest.TestCase):
    def setUp(self):
        self.data = b"authenticated opaque IPSW fixture"
        self.record = {"url": URL, "size_bytes": len(self.data),
                       "sha256": hashlib.sha256(self.data).hexdigest()}

    def download(self, response, path):
        return download_ipsw(self.record, path,
                             opener=SimpleNamespace(open=lambda *args, **kwargs: response),
                             report=lambda message: None)

    def test_verified_download_admitted_only_after_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Apple.ipsw"
            self.download(Response(self.data), path)
            self.assertEqual(path.read_bytes(), self.data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix(".ipsw.partial").exists())

    def test_invalid_transport_and_content_never_leave_admitted_or_partial_file(self):
        variants = [dict(status=206), dict(length=0), dict(encoding="gzip"),
                    dict(url="https://example.org/Restore.ipsw"),
                    dict(url="http://updates.cdn-apple.com/Restore.ipsw")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Apple.ipsw"
            for variant in variants:
                with self.subTest(variant=variant), self.assertRaises(BootInputError):
                    self.download(Response(self.data, **variant), path)
                self.assertEqual(list(Path(directory).iterdir()), [])
            for data in (self.data[:-1], self.data + b"extra", b"x" * len(self.data)):
                with self.subTest(data=data), self.assertRaises(BootInputError):
                    self.download(Response(data, length=len(self.data)), path)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_disconnect_removes_incomplete_download(self):
        class Disconnect(Response):
            def read(self, size):
                raise OSError("connection reset")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BootInputError, "before partition changes"):
                self.download(Disconnect(self.data), Path(directory) / "Apple.ipsw")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_file_symlink_or_partial_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Apple.ipsw"
            path.write_bytes(b"existing")
            with self.assertRaises(BootInputError):
                self.download(Response(self.data), path)
            self.assertEqual(path.read_bytes(), b"existing")
            path.unlink()
            path.symlink_to(Path(directory) / "absent")
            with self.assertRaises(BootInputError):
                self.download(Response(self.data), path)
            self.assertTrue(path.is_symlink())
            path.unlink()
            partial = path.with_suffix(".ipsw.partial")
            partial.write_bytes(b"other writer")
            with self.assertRaises(BootInputError):
                self.download(Response(self.data), path)
            self.assertEqual(partial.read_bytes(), b"other writer")

    def test_origin_policy_is_applied_before_following_redirect(self):
        request = urllib.request.Request(URL)
        for url in ("http://updates.cdn-apple.com/file", "https://example.com/file",
                    "https://updates.cdn-apple.com.evil.com/file", "file:///tmp/file",
                    "https://user@updates.cdn-apple.com/file",
                    "https://updates.cdn-apple.com:8443/file", URL + "#fragment"):
            with self.subTest(url=url), self.assertRaises(BootInputError):
                AppleRedirects().redirect_request(request, None, 302, "redirect", {}, url)
        self.assertEqual(apple_url(URL), URL)

    def test_image_is_read_only_and_detached_even_when_firmware_collection_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / 'Apple.ipsw'
            member = 'System.dmg.aea'
            with zipfile.ZipFile(source, 'w') as writer:
                writer.writestr(member, self.data)
            lock = {'system_image': {'member': member, 'size_bytes': len(self.data),
                                     'sha256': self.record['sha256'],
                                     'decoded': {'size_bytes': 7, 'sha256': hashlib.sha256(b'decoded').hexdigest()}}}
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if args[0] == 'decoder':
                    Path(args[-1]).write_bytes(b'decoded')
                return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [
                    {'mount-point': str(work / 'apple-system')}]}))
            selection = {'manifest': {'BuildIdentities': [{'Manifest': {'OS': {'Info': {'Path': member}}}}]}}
            with zipfile.ZipFile(source) as archive, patch('apple_inputs.inspect_ipsw', return_value=selection), \
                    patch('apple_inputs.progress'):
                with self.assertRaisesRegex(ValueError, 'bad firmware'):
                    with mounted_system_image(archive, lock, {}, work, 'decoder', run=run):
                        raise ValueError('bad firmware')
            attach = next(args for args in calls if 'attach' in args)
            self.assertIn('-readonly', attach)
            self.assertIn('-nobrowse', attach)
            self.assertEqual(calls[-1], ['/usr/bin/hdiutil', 'detach', str(work / 'apple-system')])
            self.assertEqual(list(work.iterdir()), [source])

    def test_preparation_and_retained_budgets_are_independently_required(self):
        profile_path = Path(__file__).resolve().parents[1] / "cleanroom/profiles/j713.json"
        profile = load_profile(profile_path)
        lock_path = profile_path.with_name("j713-apple-inputs.json")
        lock = load_apple_inputs(lock_path, profile)
        self.assertEqual(lock["execution_scratch_bytes"], 8 * 1024**3)
        self.assertEqual(lock["preflight_scratch_bytes"], 64 * 1024**3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock.json"
            for field in ("execution_scratch_bytes", "preflight_scratch_bytes"):
                invalid = dict(lock)
                invalid[field] -= 1
                path.write_text(json.dumps(invalid))
                with self.assertRaisesRegex(BootInputError, field):
                    load_apple_inputs(path, profile)

    def test_retained_subset_preserves_links_and_discards_large_download(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source, target = work / "Apple.ipsw", work / "restore.zip"
            link = zipfile.ZipInfo("framework/Current")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("framework/A/binary", b"boot code")
                archive.writestr(link, b"A")
                archive.writestr("System.dmg.aea", b"large system image")
            members = ["framework/A/binary", "framework/Current"]
            with patch("apple_inputs.stub_members", return_value=members):
                retain_stub_inputs(source, {}, target)
            self.assertFalse(source.exists())
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), members)
                self.assertEqual(archive.read("framework/A/binary"), b"boot code")
                self.assertEqual(archive.read(link.filename), b"A")
                self.assertEqual(archive.getinfo(link.filename).external_attr, link.external_attr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_failed_subset_validation_keeps_source_and_does_not_admit_subset(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "Apple.ipsw", Path(directory) / "restore.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("boot", b"code")
            with patch("apple_inputs.stub_members", side_effect=[["boot"], []]):
                with self.assertRaisesRegex(BootInputError, "subset"):
                    retain_stub_inputs(source, {}, target)
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_retained_budget_leaves_room_for_engine_and_rejects_active_mount(self):
        with tempfile.TemporaryDirectory() as directory, patch("apple_inputs.progress"):
            work = Path(directory)
            (work / "Recovery").write_bytes(b"recovery")
            self.assertEqual(verify_retained_workspace(work, 1024**3 + 8), 8)
            with self.assertRaisesRegex(BootInputError, "budget"):
                verify_retained_workspace(work, 1024**3 + 7)
            (work / ".apple-image-mount-active").touch()
            with self.assertRaisesRegex(BootInputError, "detach"):
                verify_retained_workspace(work, 8 * 1024**3)

    def test_workspace_preserved_until_detach_is_confirmed(self):
        workspace = AppleWorkspace()
        root = Path(workspace.name)
        marker = root / '.apple-image-mount-active'
        marker.touch()
        try:
            with self.assertLogs(level='ERROR'):
                workspace.cleanup()
            self.assertTrue(root.exists())
            marker.unlink()
        finally:
            AppleWorkspace._cleanup(root)


if __name__ == "__main__":
    unittest.main()
