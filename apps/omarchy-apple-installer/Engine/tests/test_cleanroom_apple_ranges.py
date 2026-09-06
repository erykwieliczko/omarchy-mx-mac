"""Exercise selective ZIP transport, admission and dev-only cache reuse."""
import hashlib
import io
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanroom"))
from apple_ranges import AppleRangeReader, selected_archive
from boot_inputs import BootInputError

URL = "https://updates.cdn-apple.com/build/Restore.ipsw"


class Response(io.BytesIO):
    def __init__(self, data, start, end, total):
        super().__init__(data)
        self.status = 206
        self.headers = {"Content-Length": str(end - start + 1),
                        "Content-Range": f"bytes {start}-{end}/{total}"}

    def geturl(self):
        return URL


class AppleRangeTests(unittest.TestCase):
    def setUp(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("boot", b"admitted boot code", compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("unneeded", b"x" * (8 * 1024**2))
        self.data = output.getvalue()
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            item = archive.getinfo("boot")
            record = {"size_bytes": item.file_size, "external_attr": item.external_attr,
                      "sha256": hashlib.sha256(archive.read(item)).hexdigest(),
                      "compressed_size_bytes": item.compress_size,
                      "compression": item.compress_type, "header_offset": item.header_offset}
        self.lock = {"ipsw": {"url": URL, "size_bytes": len(self.data)}, "members": {"boot": record}}
        self.transferred = 0

    def open(self, request, **kwargs):
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.get_header("Range")).groups())
        data = self.data[start:end + 1]
        self.transferred += len(data)
        return Response(data, start, end, len(self.data))

    def acquire(self, output, cache=None):
        with patch("apple_ranges.stub_members", return_value=["boot"]), patch("apple_ranges.progress"):
            return selected_archive(self.lock, {}, output, cache_directory=cache,
                                    opener=SimpleNamespace(open=self.open))

    def test_fetches_selected_member_without_downloading_unneeded_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.acquire(Path(directory) / "selected.zip")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["boot"])
                self.assertEqual(archive.read("boot"), b"admitted boot code")
            self.assertLess(self.transferred, len(self.data) // 2)

    def test_unaligned_bulk_reads_keep_bounded_read_ahead(self):
        # ZIP member bodies rarely start on a 1 MiB boundary. A cached prefix
        # must not turn the rest of every bulk read into a separate request.
        block = 1024**2
        total = 130 * block + 517
        for offset in (0, 123, block - 1):
            requests = []
            def fetch(request, **kwargs):
                start, end = map(int, request.get_header("Range")[6:].split("-"))
                requests.append((start, end))
                self.assertLessEqual(end - start + 1, 32 * block)
                self.assertLess(end, total)
                data = b"".join(bytes([index % 251]) * min(block, total - index * block)
                                for index in range(start // block, end // block + 1))
                return Response(data, start, end, total)
            with self.subTest(offset=offset), AppleRangeReader(
                    {"url": URL, "size_bytes": total}, opener=SimpleNamespace(open=fetch)) as reader:
                reader.seek(offset)
                for index in range(128):
                    data = reader.read(block)
                    start_block, within = divmod(offset + index * block, block)
                    expected = (bytes([start_block % 251]) * (block - within)
                                + bytes([(start_block + 1) % 251]) * within)
                    self.assertEqual(data, expected)
                self.assertLessEqual(len(requests), 5)
                self.assertLessEqual(len(reader.blocks), 32)
                reader.seek(total - 517)
                self.assertEqual(reader.read(block), bytes([130 % 251]) * 517)
                self.assertEqual(reader.read(1), b"")

    def test_range_protocol_must_match_exact_request(self):
        variants = [("status", 200), ("Content-Range", "bytes 0-2/3"),
                    ("Content-Length", "0"), ("Content-Encoding", "gzip")]
        for field, value in variants:
            def bad_open(request, **kwargs):
                response = self.open(request)
                if field == "status":
                    response.status = value
                else:
                    response.headers[field] = value
                return response
            with self.subTest(field=field), AppleRangeReader(
                    self.lock["ipsw"], opener=SimpleNamespace(open=bad_open)) as reader:
                with self.assertRaises(BootInputError):
                    reader.read(1)

    def test_wrong_member_hash_never_admitted_or_cached(self):
        self.lock["members"]["boot"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path, cache = Path(directory) / "selected.zip", Path(directory) / "cache"
            with self.assertRaisesRegex(BootInputError, "SHA-256"):
                self.acquire(path, cache)
            self.assertFalse(path.exists())
            self.assertEqual(list(cache.iterdir()), [])

    def test_cache_hit_is_verified_and_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            self.acquire(root / "first.zip", cache)
            network_bytes = self.transferred
            def clone(args, **kwargs):
                shutil.copyfile(args[-2], args[-1])
            with patch("apple_ranges.subprocess.run", side_effect=clone):
                self.acquire(root / "second.zip", cache)
            self.assertEqual(network_bytes, self.transferred)
            self.acquire(root / "normal.zip")
            self.assertGreater(self.transferred, network_bytes)
            cached = next(cache.iterdir())
            with zipfile.ZipFile(cached, "w") as archive:
                archive.writestr("boot", b"corrupted boot code")
            with patch("apple_ranges.subprocess.run", side_effect=clone):
                with self.assertRaises(BootInputError):
                    self.acquire(root / "bad.zip", cache)
            self.assertFalse((root / "bad.zip").exists())

    def test_cache_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(BootInputError, "cache directory"):
                self.acquire(root / "selected.zip", cache)

    def test_zip_directory_read_is_bounded(self):
        with AppleRangeReader(self.lock["ipsw"], opener=SimpleNamespace(open=self.open)) as reader:
            reader.size = 1024**3
            with self.assertRaisesRegex(BootInputError, "window"):
                reader.read(128 * 1024**2)
            self.assertEqual(self.transferred, 0)
