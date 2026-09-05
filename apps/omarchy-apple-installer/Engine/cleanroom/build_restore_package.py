# SPDX-License-Identifier: MIT
"""Create a deterministic Apple stub archive from an admitted full IPSW."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from boot_inputs import BootInputError, load_profile, stub_members


def descriptor(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as reader:
        while chunk := reader.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def build(ipsw, expected_sha256, profile, output):
    ipsw, output = Path(ipsw), Path(output)
    if ipsw.is_symlink() or not ipsw.is_file():
        raise BootInputError("IPSW must be a regular file")
    source = descriptor(ipsw)
    if source["sha256"] != expected_sha256:
        raise BootInputError("full IPSW digest differs from admitted build input")
    if output.exists() or output.is_symlink():
        raise BootInputError("refusing to replace an Apple restore package")
    with tempfile.TemporaryDirectory(prefix=".restore-package-", dir=output.parent) as directory:
        package = Path(directory) / "apple-restore.zip"
        with zipfile.ZipFile(ipsw) as archive:
            members = stub_members(archive, profile)
            with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_STORED) as writer:
                for name in members:
                    original = archive.getinfo(name)
                    item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    item.create_system = 3
                    item.external_attr = original.external_attr
                    item.file_size = original.file_size
                    with archive.open(original) as reader, writer.open(item, "w", force_zip64=True) as target:
                        shutil.copyfileobj(reader, target, 1024 * 1024)
        with zipfile.ZipFile(package) as archive:
            if stub_members(archive, profile) != members or archive.testzip() is not None:
                raise BootInputError("Apple restore subset did not verify")
        receipt = {"schema_version": 1, "profile": profile, "ipsw": source,
                   "restore_package": descriptor(package), "members": members}
        os.chmod(package, 0o444)
        os.link(package, output)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ipsw", type=Path)
    parser.add_argument("ipsw_sha256")
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.ipsw, args.ipsw_sha256, load_profile(args.profile), args.output),
                     indent=2, sort_keys=True))
