# SPDX-License-Identifier: MIT
"""Acquire pinned Apple inputs in private scratch space before disk mutation."""

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import weakref

from boot_inputs import BootInputError, _hex, _member, inspect_ipsw


def apple_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "updates.cdn-apple.com"
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or not parsed.path.startswith("/")
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError("unexpected Apple download origin")
    except (TypeError, ValueError) as error:
        raise BootInputError("invalid Apple download URL") from error
    return url


class AppleRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        apple_url(url)
        return super().redirect_request(request, fp, code, message, headers, url)


def load_apple_inputs(path, profile):
    lock = json.loads(Path(path).read_text())
    if (lock.get("schema_version") != 1
            or lock.get("device_identifier") != profile["device_identifier"]
            or lock.get("product_type") != profile["product_type"]
            or lock.get("firmware_build") != profile["firmware"]["build"]):
        raise BootInputError("Apple input lock differs from model profile")
    apple_url(lock["ipsw"]["url"])
    for record in (lock["ipsw"], lock["system_image"], lock["system_image"]["decoded"]):
        if (type(record["size_bytes"]) is not int or record["size_bytes"] <= 0
                or not _hex(record["sha256"], 64)):
            raise BootInputError("invalid Apple input descriptor")
    if (type(lock["execution_scratch_bytes"]) is not int
            or lock["execution_scratch_bytes"] < 64 * 1024**3):
        raise BootInputError("Apple input scratch budget is too small")
    return lock


def verify_file(path, record):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as reader:
        status = os.fstat(reader.fileno())
        if not stat.S_ISREG(status.st_mode) or status.st_size != record["size_bytes"]:
            raise BootInputError("Apple decoded system image size or file type mismatch")
        digest = hashlib.sha256()
        size = 0
        while chunk := reader.read(min(4 * 1024 * 1024, record["size_bytes"] - size + 1)):
            size += len(chunk)
            digest.update(chunk)
            if size > record["size_bytes"]:
                break
        if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
            raise BootInputError("Apple decoded system image SHA-256 mismatch")


def progress(message):
    logging.info("Apple inputs: %s", message)
    print("Apple inputs: " + message, flush=True)


class AppleWorkspace:
    """Never recursively clean up an image mount whose detach is unconfirmed."""
    def __init__(self):
        self.name = tempfile.mkdtemp(prefix="omarchy-apple-inputs-")
        self.finalizer = weakref.finalize(self, self._cleanup, Path(self.name))

    @staticmethod
    def _cleanup(path):
        if ((path / ".apple-image-mount-active").exists()
                or os.path.ismount(path / "apple-system")):
            logging.error("Preserving Apple scratch directory with unconfirmed detach: %s", path)
            return
        shutil.rmtree(path)

    def cleanup(self):
        self.finalizer()


def download_ipsw(record, destination, *, opener=None, report=progress):
    """Stream once, validate size and digest, then admit by atomic rename.

    The descriptor is part of authenticated engine metadata and must match the
    bundled source lock. Neither the installed macOS nor mutable online catalog
    entries select or replace the admitted build at runtime.
    """
    url = apple_url(record["url"])
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BootInputError("refusing to overwrite an Apple input")
    pending = destination.with_name(destination.name + ".partial")
    opener = opener or urllib.request.build_opener(AppleRedirects())
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    report("Downloading macOS restore inputs directly from Apple (%.1f GiB)"
           % (record["size_bytes"] / 1024**3))
    created = False
    try:
        with pending.open("xb") as writer:
            created = True
            os.chmod(pending, 0o600)
            with opener.open(request, timeout=60) as response:
                apple_url(response.geturl())
                if (response.status != 200
                        or response.headers.get("Content-Encoding", "identity") != "identity"
                        or response.headers.get("Content-Length") != str(record["size_bytes"])):
                    raise BootInputError("unexpected Apple download response")
                size = 0
                digest = hashlib.sha256()
                previous = time.monotonic()
                while chunk := response.read(min(4 * 1024 * 1024, record["size_bytes"] - size + 1)):
                    size += len(chunk)
                    if size > record["size_bytes"]:
                        raise BootInputError("Apple download exceeds its admitted size")
                    writer.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - previous >= 10:
                        report("Downloaded %.1f / %.1f GiB (%.0f%%)" % (
                            size / 1024**3, record["size_bytes"] / 1024**3,
                            size * 100 / record["size_bytes"]))
                        previous = time.monotonic()
                if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
                    raise BootInputError("Apple download size or SHA-256 mismatch")
        pending.rename(destination)
    except (OSError, urllib.error.URLError) as error:
        raise BootInputError("Apple download failed before partition changes: " + str(error)) from error
    finally:
        if created:
            pending.unlink(missing_ok=True)
    report("Apple restore download verified")
    return destination


@contextmanager
def mounted_system_image(archive, lock, profile, workspace, decoder, *, run=subprocess.run):
    """Decode and mount only the pinned OS member, read-only and hidden."""
    work = Path(workspace)
    record = lock["system_image"]
    selection = inspect_ipsw(archive, profile)
    components = selection["manifest"]["BuildIdentities"][0]["Manifest"]
    if components["OS"]["Info"]["Path"] != record["member"]:
        raise BootInputError("Apple system image differs from selected build identity")
    member = _member(archive, record["member"], record["size_bytes"])
    if member.file_size != record["size_bytes"]:
        raise BootInputError("unexpected Apple system image size")
    source, decoded = work / "System.dmg.aea", work / "System.dmg"
    mount = work / "apple-system"
    mount.mkdir(mode=0o700)
    marker = work / ".apple-image-mount-active"
    attached = False
    try:
        progress("Extracting the Apple system image for Wi-Fi firmware")
        digest = hashlib.sha256()
        with archive.open(member) as reader, source.open("xb") as writer:
            os.chmod(source, 0o600)
            while chunk := reader.read(4 * 1024 * 1024):
                writer.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            raise BootInputError("Apple system image SHA-256 mismatch")
        progress("Decoding and verifying the Apple system image")
        run([str(decoder), str(source), record["sha256"], str(decoded)], check=True)
        source.unlink()
        # Apple's OS member decodes to raw APFS (UDRW), with no UDIF checksum.
        # Pin the authenticated plaintext rather than treating an unsupported
        # hdiutil verification as success or silently skipping validation.
        verify_file(decoded, record["decoded"])
        marker.touch(exist_ok=False)
        attached = True
        result = run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                      "-owners", "off", "-mountpoint", str(mount), "-plist", str(decoded)],
                     check=True, capture_output=True)
        entities = plistlib.loads(result.stdout).get("system-entities", [])
        if sum(entity.get("mount-point") == str(mount) for entity in entities) != 1:
            raise BootInputError("Apple image did not mount at its private mount point")
        yield mount
    finally:
        if attached:
            # Failure to detach is fatal before the partition transaction. Never
            # recursively delete a workspace while an Apple image is mounted.
            run(["/usr/bin/hdiutil", "detach", str(mount)], check=True)
            marker.unlink()
        source.unlink(missing_ok=True)
        decoded.unlink(missing_ok=True)
        mount.rmdir()
