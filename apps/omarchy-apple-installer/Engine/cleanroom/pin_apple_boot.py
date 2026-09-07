# SPDX-License-Identifier: MIT
"""Admit boot-only member hashes directly from an immutable Apple HTTPS IPSW.

This is a maintainer tool, never runtime discovery. Review the resulting lock
before bundling it into an authenticated engine. It ships metadata, not blobs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import zipfile

from apple_ranges import AppleRangeReader
from boot_inputs import ipsw_boot_profiles, stub_members


def pin(url, size_bytes, version, build, output):
    firmware = {"version": version, "build": build, "variant": "macOS Customer",
                "restore_behavior": "Erase"}
    descriptor = {"url": url, "size_bytes": size_bytes}
    with AppleRangeReader(descriptor) as source, zipfile.ZipFile(source) as archive:
        original_read, cache = archive.read, {}

        def read(name, *args, **kwargs):
            key = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if key not in cache:
                cache[key] = original_read(name, *args, **kwargs)
            return cache[key]

        archive.read = read
        products, profiles = ipsw_boot_profiles(archive, {"firmware": firmware})
        restore = plistlib.loads(archive.read("RestoreVersion.plist"))["RestoreLongVersion"]
        brain = "BootabilityBundle/Restore/Bootability/System/Library/CoreServices/RestoreVersion.plist"
        if plistlib.loads(archive.read(brain))["RestoreLongVersion"] != restore:
            raise ValueError("Apple bootability and restore versions differ")
        names, identities = set(), []
        for profile in profiles:
            closure = stub_members(archive, profile)
            identities.append({**{key: profile[key] for key in ("device_class", "board_id", "chip_id")},
                               "members": closure})
            names.update(closure)
        print(json.dumps({"identities": len(identities), "members": len(names),
                          "restore_version": restore,
                          "compressed_bytes": sum(archive.getinfo(n).compress_size for n in names)}), flush=True)
        members = {}
        for index, name in enumerate(sorted(names, key=lambda n: archive.getinfo(n).header_offset)):
            info = archive.getinfo(name)
            if info.compress_type not in (0, 8) or info.flag_bits != 0:
                raise ValueError("unsupported Apple ZIP member: " + name)
            digest = hashlib.sha256()
            with archive.open(info) as reader:
                while chunk := reader.read(4 * 1024**2):
                    digest.update(chunk)
            members[name] = {"size_bytes": info.file_size, "sha256": digest.hexdigest(),
                             "external_attr": info.external_attr,
                             "compressed_size_bytes": info.compress_size,
                             "compression": info.compress_type, "header_offset": info.header_offset}
            if index % 30 == 0:
                print("Pinned", index + 1, "/", len(names), flush=True)
    lock = {"schema_version": 1, "kind": "apple-boot-only", "ipsw": descriptor,
            "firmware": firmware, "restore_version": restore, "supported_products": products,
            "boot_identities": identities, "members": dict(sorted(members.items()))}
    output.write_text(json.dumps(lock, indent=2) + "\n")
    print("Admitted Apple member hashes; network bytes:", source.network_bytes, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("size_bytes", type=int)
    parser.add_argument("version")
    parser.add_argument("build")
    parser.add_argument("output", type=Path)
    pin(**vars(parser.parse_args()))
