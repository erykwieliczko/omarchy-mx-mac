# SPDX-License-Identifier: MIT
"""Selective Apple ZIP reads with signed member admission and optional dev cache."""
from collections import OrderedDict
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from apple_inputs import AppleRedirects, apple_url, progress
from boot_inputs import BootInputError, stub_members


class AppleRangeReader(io.RawIOBase):
    block_size = 1024 * 1024
    cache_blocks = 32

    def __init__(self, record, *, opener=None, report=progress):
        super().__init__()
        self.url = apple_url(record["url"])
        self.size = record["size_bytes"]
        self.position = 0
        self.blocks = OrderedDict()
        self.opener = opener or urllib.request.build_opener(AppleRedirects())
        self.network_bytes = 0
        self.last_report = time.monotonic()
        self.report = report

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        bases = {os.SEEK_SET: 0, os.SEEK_CUR: self.position, os.SEEK_END: self.size}
        if whence not in bases or bases[whence] + offset < 0:
            raise ValueError("invalid Apple ZIP seek")
        self.position = bases[whence] + offset
        return self.position

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.position
        size = min(size, max(0, self.size - self.position))
        # Bound ZIP directory/header parsing before trusting remote structures.
        if size > 32 * 1024**2:
            raise BootInputError("Apple ZIP read exceeds bounded window")
        result = bytearray()
        while len(result) < size:
            index, offset = divmod(self.position, self.block_size)
            if index not in self.blocks:
                count = self.cache_blocks if size - len(result) >= self.block_size else 1
                start = index * self.block_size
                end = min(start + count * self.block_size, self.size) - 1
                request = urllib.request.Request(self.url, headers={
                    "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
                with self.opener.open(request, timeout=60) as response:
                    apple_url(response.geturl())
                    expected = end - start + 1
                    if (response.status != 206
                            or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}"
                            or response.headers.get("Content-Length") != str(expected)
                            or response.headers.get("Content-Encoding", "identity") != "identity"):
                        raise BootInputError("Apple server did not return the exact requested ZIP range")
                    data = response.read(expected + 1)
                    if len(data) != expected:
                        raise BootInputError("Apple ZIP range was truncated or oversized")
                self.network_bytes += len(data)
                for i in range(0, len(data), self.block_size):
                    self.blocks[index + i // self.block_size] = data[i:i + self.block_size]
                while len(self.blocks) > self.cache_blocks:
                    self.blocks.popitem(last=False)
                if time.monotonic() - self.last_report >= 10:
                    self.report("Downloaded %.1f GiB of selected Apple ZIP ranges"
                                % (self.network_bytes / 1024**3))
                    self.last_report = time.monotonic()
            block = self.blocks[index]
            self.blocks.move_to_end(index)
            chunk = block[offset:offset + size - len(result)]
            result.extend(chunk)
            self.position += len(chunk)
        return bytes(result)


def validate_members(archive, lock, *, verify_contents=False):
    infos = archive.infolist()
    for name, record in lock["members"].items():
        matches = [item for item in infos if item.filename == name]
        if len(matches) != 1:
            raise BootInputError("missing or duplicate selected Apple ZIP member: " + name)
        item = matches[0]
        if item.file_size != record["size_bytes"] or item.external_attr != record["external_attr"]:
            raise BootInputError("Apple ZIP member descriptor differs from signed input: " + name)
        if not verify_contents and (
                item.compress_size != record["compressed_size_bytes"]
                or item.compress_type != record["compression"]
                or item.header_offset != record["header_offset"] or item.flag_bits != 0):
            raise BootInputError("Apple ZIP range metadata differs from signed input: " + name)
        if verify_contents:
            with archive.open(item) as reader:
                copy_verified(reader, None, record, name)


def copy_verified(reader, writer, record, name):
    digest = hashlib.sha256()
    size = 0
    while chunk := reader.read(min(1024 * 1024, record["size_bytes"] - size + 1)):
        size += len(chunk)
        if size > record["size_bytes"]:
            raise BootInputError("oversized Apple ZIP member: " + name)
        digest.update(chunk)
        if writer is not None:
            writer.write(chunk)
    if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
        raise BootInputError("Apple ZIP member size or SHA-256 mismatch: " + name)


def selected_archive(lock, profile, destination, *, cache_directory=None, opener=None):
    """Hash every selected member before admitting a private local ZIP.

    A cache is enabled only by an explicit development engine build. Normal
    engines never consult it. Cache entries contain ZIP bytes, not extracted
    filesystem links, and never substitute for signed member validation.
    """
    destination = Path(destination)
    cache = None
    if cache_directory is not None:
        directory = Path(cache_directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = directory.lstat()
        if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.geteuid() or status.st_mode & 0o077:
            raise BootInputError("unsafe development Apple cache directory")
        # Include the selected-member lock so future profiles cannot reuse a
        # different selection from the same IPSW.
        selection = hashlib.sha256(json.dumps(lock["members"], sort_keys=True).encode()).hexdigest()
        cache = directory / (selection + ".zip")
        if cache.exists() or cache.is_symlink():
            status = cache.lstat()
            if not stat.S_ISREG(status.st_mode) or status.st_uid != os.geteuid() or status.st_mode & 0o022:
                raise BootInputError("unsafe development Apple cache entry")
            progress("Reusing development Apple cache; verifying selected member hashes")
            if destination.exists() or destination.is_symlink():
                raise BootInputError("refusing to replace private Apple inputs")
            # APFS copy-on-write keeps subsequent cache changes independent.
            try:
                subprocess.run(["/bin/cp", "-c", str(cache), str(destination)], check=True)
                with zipfile.ZipFile(destination) as archive:
                    if set(archive.namelist()) != set(lock["members"]):
                        raise BootInputError("development cache has an unexpected Apple selection")
                    validate_members(archive, lock, verify_contents=True)
                    stub_members(archive, profile)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination
    progress("Downloading only the selected Apple ZIP members (%.1f GiB compressed)"
             % (sum(r["compressed_size_bytes"] for r in lock["members"].values()) / 1024**3))
    with tempfile.TemporaryDirectory(prefix=".apple-ranges-", dir=destination.parent) as temporary:
        pending = Path(temporary) / "selected.zip"
        with AppleRangeReader(lock["ipsw"], opener=opener) as source, zipfile.ZipFile(source) as archive:
            validate_members(archive, lock)
            # Physical ZIP order avoids repeatedly fetching adjacent ranges.
            names = sorted(lock["members"], key=lambda name: archive.getinfo(name).header_offset)
            with zipfile.ZipFile(pending, "x", compression=zipfile.ZIP_STORED) as writer:
                for name in names:
                    record = lock["members"][name]
                    item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    item.create_system = 3
                    item.external_attr = record["external_attr"]
                    item.file_size = record["size_bytes"]
                    with archive.open(name) as reader, writer.open(item, "w", force_zip64=True) as target:
                        copy_verified(reader, target, record, name)
        progress("Selected Apple inputs verified (%.1f GiB transferred)" % (source.network_bytes / 1024**3))
        with zipfile.ZipFile(pending) as archive:
            stub_members(archive, profile)
        os.chmod(pending, 0o600)
        if cache is not None:
            os.link(pending, cache)
            progress("Verified Apple inputs saved in the development cache")
        os.link(pending, destination)
    return destination
