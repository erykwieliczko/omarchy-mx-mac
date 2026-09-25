# SPDX-License-Identifier: MIT
"""Prepare a verified Recovery image before any partition operation."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from boot_inputs import BootInputError, inspect_ipsw


def prepare_recovery(archive, profile, destination, decoder, *, run=subprocess.run):
    """Decode only the selected BaseSystem from an already admitted IPSW.

    The returned hashes bind the decoded image and its matching opaque Apple
    authentication companions. This does not personalize or bless a stub.
    The decoder is a fixed executable inside the admitted engine bundle, never
    a path taken from downloaded metadata.
    """
    selection = inspect_ipsw(archive, profile)
    recovery = selection["recovery"]
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BootInputError("refusing to overwrite Recovery output")
    with tempfile.TemporaryDirectory(prefix=".omarchy-recovery-", dir=destination.parent) as directory:
        directory = Path(directory)
        source = directory / Path(recovery["image"]).name
        digest = hashlib.sha256()
        copied = 0
        with archive.open(recovery["image"]) as reader, source.open("xb") as writer:
            os.chmod(source, 0o600)
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
        if copied != recovery["size_bytes"]:
            raise BootInputError("incomplete Recovery image extraction")
        input_digest = digest.hexdigest()
        decoded = directory / "decoded.dmg"
        if recovery["format"] == "aea":
            run([str(decoder), str(source), input_digest, str(decoded)], check=True)
        else:
            os.rename(source, decoded)
        if not decoded.is_file() or decoded.is_symlink():
            raise BootInputError("Recovery decoder did not produce a regular file")
        # Verify the image's own checksum before it can become a paired Recovery
        # input. Apple's later boot authentication is a separate native step.
        run(["/usr/bin/hdiutil", "verify", "-quiet", str(decoded)], check=True)
        digest = hashlib.sha256()
        with decoded.open("rb") as reader:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
        receipt = {
            "schema_version": 1,
            "device_identifier": profile["device_identifier"],
            "firmware_build": profile["firmware"]["build"],
            "manifest_sha256": selection["manifest_sha256"],
            "input": {"member": recovery["image"], "size_bytes": copied,
                      "sha256": input_digest, "format": recovery["format"]},
            "decoded": {"size_bytes": decoded.stat().st_size, "sha256": digest.hexdigest()},
            "authentication": {},
        }
        for role in ("root_hash", "trustcache"):
            path = recovery[role]
            digest = hashlib.sha256()
            size = 0
            with archive.open(path) as reader:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            receipt["authentication"][role] = {
                "member": path, "size_bytes": size, "sha256": digest.hexdigest(),
            }
        os.chmod(decoded, 0o600)
        os.link(decoded, destination)
        return receipt
