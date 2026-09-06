"""Exercise Apple download admission without network access or physical disks."""
import hashlib
import io
import plistlib
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
from apple_inputs import AppleRedirects, AppleWorkspace, apple_url, download_ipsw, mounted_system_image
from boot_inputs import BootInputError


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
