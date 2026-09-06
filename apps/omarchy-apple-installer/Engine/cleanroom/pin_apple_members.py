# SPDX-License-Identifier: MIT
"""Pin selected ZIP members from the already admitted full Apple IPSW."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from apple_inputs import verify_file
from boot_inputs import load_profile, stub_members


def pin(ipsw, profile_path, lock_path):
    lock = json.loads(lock_path.read_text())
    verify_file(ipsw, lock["ipsw"])
    profile = load_profile(profile_path)
    with zipfile.ZipFile(ipsw) as archive:
        names = sorted(set(stub_members(archive, profile)) | {lock["system_image"]["member"]})
        members = {}
        for name in names:
            info = archive.getinfo(name)
            if info.compress_type not in (0, 8) or info.flag_bits != 0:
                raise ValueError("unsupported selected ZIP member: " + name)
            digest = hashlib.sha256()
            with archive.open(info) as reader:
                while chunk := reader.read(4 * 1024 * 1024):
                    digest.update(chunk)
            members[name] = {"size_bytes": info.file_size, "sha256": digest.hexdigest(),
                             "external_attr": info.external_attr,
                             "compressed_size_bytes": info.compress_size,
                             "compression": info.compress_type,
                             "header_offset": info.header_offset}
    lock["schema_version"] = 2
    lock["members"] = members
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    print(json.dumps({"selected_members": len(members), "compressed_bytes": sum(
        record["compressed_size_bytes"] for record in members.values())}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ipsw", "profile_path", "lock_path"):
        parser.add_argument(name, type=Path)
    pin(**vars(parser.parse_args()))
