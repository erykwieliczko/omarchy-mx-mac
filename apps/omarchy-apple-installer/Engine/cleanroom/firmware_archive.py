# SPDX-License-Identifier: MIT
"""Verify the complete generated vendor CPIO and installed-tree tar together."""
from pathlib import Path, PurePosixPath
import stat
import tarfile

from boot_inputs import BootInputError


def cpio_files(data):
    files, inodes = {}, {}
    offset = 0
    trailer = False
    while offset < len(data):
        start = offset
        header = data[start:start + 110]
        if len(header) != 110 or header[:6] != b"070701":
            raise BootInputError("malformed vendor firmware CPIO header")
        try:
            fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        except ValueError as error:
            raise BootInputError("invalid vendor firmware CPIO fields") from error
        inode, mode, links, size, namesize = fields[0], fields[1], fields[4], fields[6], fields[11]
        raw = data[start + 110:start + 110 + namesize]
        if not 1 <= namesize <= 4096 or len(raw) != namesize or raw[-1:] != b"\0" or b"\0" in raw[:-1]:
            raise BootInputError("invalid vendor firmware CPIO name")
        name = raw[:-1].decode("ascii")
        offset = (start + 110 + namesize + 3) & ~3
        body = data[offset:offset + size]
        if len(body) != size:
            raise BootInputError("truncated vendor firmware CPIO body")
        offset = (offset + size + 3) & ~3
        if name == "TRAILER!!!":
            if size or any(data[offset:]):
                raise BootInputError("unexpected trailing vendor firmware archive")
            trailer = True
            break
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name:
            raise BootInputError("unsafe vendor firmware CPIO path")
        if stat.S_ISDIR(mode):
            if size:
                raise BootInputError("nonempty vendor firmware directory")
            continue
        if not stat.S_ISREG(mode) or name in files or links < 1:
            raise BootInputError("invalid or duplicate vendor firmware member")
        key = (fields[7], fields[8], inode)
        if key in inodes:
            previous, count, seen = inodes[key]
            if size or count != links:
                raise BootInputError("inconsistent vendor firmware hard link")
            inodes[key] = (previous, count, seen + 1)
            body = previous
        else:
            inodes[key] = (body, links, 1)
        files[name] = body
    if not trailer or any(count != seen for _, count, seen in inodes.values()):
        raise BootInputError("incomplete vendor firmware CPIO archive")
    return files


def verify_package(directory, firmware):
    directory = Path(directory)
    expected = {name: value.data for name, value in firmware}
    if len(expected) != len(firmware):
        raise BootInputError("duplicate generated firmware name")
    with tarfile.open(directory / "firmware.tar", "r:") as archive:
        members = archive.getmembers()
        if len(members) != len(expected) or {member.name for member in members} != expected.keys():
            raise BootInputError("installed firmware tar inventory differs")
        for member in members:
            if not (member.isfile() or member.islnk()) or archive.extractfile(member).read() != expected[member.name]:
                raise BootInputError("installed firmware tar content differs: " + member.name)
    expected_cpio = {"vendorfw/" + name: data for name, data in expected.items()}
    expected_cpio["vendorfw/.vendorfw.manifest"] = (directory / "manifest.txt").read_bytes()
    if cpio_files((directory / "firmware.cpio").read_bytes()) != expected_cpio:
        raise BootInputError("firmware initramfs content differs from installed tree")
