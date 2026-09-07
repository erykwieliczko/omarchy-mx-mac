# SPDX-License-Identifier: MIT
"""Select authenticated Apple boot inputs compatible with the actual Mac's SFR."""
import hashlib
import json
from pathlib import Path
import plistlib
import re

from apple_inputs import validate_member_lock
from boot_inputs import BootInputError, apple_boot_identity, select_apple_boot_profile, _hex, _member


def restore_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6}){4},[0-9]{1,6}", value):
        raise BootInputError("Cannot read a valid system firmware (SFR) version; stop before partition changes")
    return tuple(int(part) for part in re.split(r"[.,]", value))


def load_boot_builds(directory, native_lock, profile):
    directory = Path(directory)
    catalog = json.loads((directory / "apple-boot-builds.json").read_text())
    if (catalog.get("schema_version") != 1 or set(catalog) != {"schema_version", "builds"}
            or not isinstance(catalog["builds"], list) or not 1 <= len(catalog["builds"]) <= 16):
        raise BootInputError("invalid Apple boot build catalog")
    builds, seen = [], set()
    for entry in catalog["builds"]:
        if not isinstance(entry, dict) or set(entry) != {"file", "sha256", "firmware", "restore_version"}:
            raise BootInputError("invalid Apple boot build descriptor")
        name = entry["file"]
        if (not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9-]+\.json", name)
                or name in seen or not _hex(entry["sha256"], 64)):
            raise BootInputError("invalid Apple boot lock reference")
        seen.add(name)
        data = (directory / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise BootInputError("Apple boot lock differs from authenticated catalog")
        lock = json.loads(data)
        firmware = entry["firmware"]
        if (not isinstance(firmware, dict) or set(firmware) != {"version", "build", "variant", "restore_behavior"}
                or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", firmware.get("version", ""))
                or not re.fullmatch(r"[0-9]+[A-Z][0-9]+[a-z]?", firmware.get("build", ""))
                or firmware["variant"] != "macOS Customer" or firmware["restore_behavior"] != "Erase"):
            raise BootInputError("invalid Apple boot firmware version")
        restore_version(entry["restore_version"])
        if name == "j713-apple-inputs.json":
            if lock != native_lock or firmware != profile["firmware"]:
                raise BootInputError("native Apple boot inputs differ from Linux baseline")
        elif (lock.get("schema_version") != 1 or lock.get("kind") != "apple-boot-only"
                or lock.get("firmware") != firmware or lock.get("restore_version") != entry["restore_version"]
                or "system_image" in lock):
            raise BootInputError("invalid boot-only Apple input lock")
        validate_member_lock(lock)
        apple_boot_identity(lock, None)
        for identity in lock["boot_identities"]:
            if "RestoreVersion.plist" not in identity["members"]:
                raise BootInputError("Apple boot identity has no pinned restore version")
        builds.append((entry, lock))
    return catalog, builds


def select_boot_build(builds, profile, host, *, allow_fallback=True):
    current = restore_version(getattr(host, "sfr_full_ver", None))
    matching = []
    for entry, lock in builds:
        if not allow_fallback and entry["firmware"] != profile["firmware"]:
            continue
        try:
            boot_profile = select_apple_boot_profile(profile, lock, host)
        except BootInputError:
            continue
        matching.append((entry, lock, {**boot_profile, "firmware": entry["firmware"]}))
    if not matching:
        raise BootInputError("No admitted Apple restore build contains the actual Mac's boot identity")
    matching.sort(key=lambda item: restore_version(item[0]["restore_version"]), reverse=True)
    for entry, lock, boot_profile in matching:
        if restore_version(entry["restore_version"]) <= current:
            return entry, lock, boot_profile
    minimum = matching[-1][0]
    raise BootInputError(
        "Apple system firmware (SFR) update required before installation: current %s, need %s. "
        "Update macOS to %s or newer using Software Update, restart, then try again. "
        "No partitions have been changed." % (host.sfr_full_ver, minimum["restore_version"],
                                               minimum["firmware"]["version"]))


def verify_boot_version(archive, entry, host):
    """Check selected, hash-verified Apple version bytes before any disk work."""
    for name in ("RestoreVersion.plist",
                 "BootabilityBundle/Restore/Bootability/System/Library/CoreServices/RestoreVersion.plist"):
        actual = plistlib.loads(archive.read(_member(archive, name, 1024**2))).get("RestoreLongVersion")
        if actual != entry["restore_version"]:
            raise BootInputError("Apple restore version differs from selected boot catalog")
    if restore_version(actual) > restore_version(getattr(host, "sfr_full_ver", None)):
        raise BootInputError("System firmware changed during Apple preparation; restart installation")


def maximum_boot_selection_bytes(builds):
    return max(sum(lock["members"][name]["size_bytes"] for name in identity["members"])
               for _, lock in builds for identity in lock["boot_identities"])
