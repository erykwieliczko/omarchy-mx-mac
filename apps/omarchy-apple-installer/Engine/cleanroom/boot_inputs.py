# SPDX-License-Identifier: MIT
"""Pure validation/assembly for cleanroom boot inputs, without disk access.

Profiles describe models, not individual installations. Admission through the
signed catalog and privileged plan remains the responsibility of the engine.
An IPSW manifest digest is not interchangeable with an Image4 payload digest.
"""

import copy
import hashlib
import json
import plistlib
import re
import stat
import uuid
from pathlib import Path, PurePosixPath


class BootInputError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise BootInputError(message)


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch(
        rf"[0-9a-f]{{{size}}}", value
    ) is not None


def load_profile(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate profile key")
            result[key] = value
        return result

    try:
        profile = json.loads(Path(path).read_text(), object_pairs_hook=unique_object)
        _require(set(profile) == {
            "schema_version", "device_identifier", "product_type", "device_class",
            "board_id", "chip_id", "firmware", "sources", "boot_format", "wifi",
        }, "invalid profile fields")
        _require(type(profile["schema_version"]) is int
                 and profile["schema_version"] == 1, "unsupported profile schema")
        device = profile["device_identifier"]
        _require(isinstance(device, str) and re.fullmatch(r"apple,j[0-9]+[a-z]*", device),
                 "invalid device identifier")
        _require(profile["device_class"] == device.removeprefix("apple,") + "ap",
                 "device class does not match model")
        _require(isinstance(profile["product_type"], str)
                 and re.fullmatch(r"Mac[0-9]+,[0-9]+", profile["product_type"]),
                 "invalid product type")
        for key in ("board_id", "chip_id"):
            _require(type(profile[key]) is int and 0 < profile[key] < 65536,
                     "invalid " + key)
        firmware = profile["firmware"]
        _require(set(firmware) == {"version", "build", "variant", "restore_behavior"},
                 "invalid firmware fields")
        _require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", firmware["version"])
                 and re.fullmatch(r"[0-9]+[A-Z][0-9]+[a-z]?", firmware["build"]),
                 "invalid firmware version")
        _require(firmware["variant"] == "macOS Customer"
                 and firmware["restore_behavior"] == "Erase",
                 "unsupported restore variant")
        _require(profile["boot_format"] == "m1n1-uboot-grub-apple-download-v2",
                 "unsupported boot format")
        sources = profile["sources"]
        _require(set(sources) == {"m1n1", "u_boot", "grub", "linux", "enablement"},
                 "incomplete cleanroom source graph")
        _require(all(_hex(revision, 40) for revision in sources.values()),
                 "invalid source revision")
        wifi = profile["wifi"]
        _require(isinstance(wifi, dict) and set(wifi) == {"source_directory", "files"},
                 "invalid Wi-Fi profile")
        _path(wifi["source_directory"])
        _require(((device == "apple,j700" and wifi["source_directory"] ==
                   "System/Library/DriverExtensions/com.apple.AppleSunriseWLAN.dext/IZUBA")
                  or (device != "apple,j700" and wifi["source_directory"].startswith("usr/share/firmware/wifi/")))
                 and isinstance(wifi["files"], dict)
                 and len(wifi["files"]) == (9 if device == "apple,j700" else 6),
                 "invalid Wi-Fi source closure")
        for destination, source in wifi["files"].items():
            _path(destination)
            _path(source)
            _require(destination.startswith("sunrise/" if device == "apple,j700" else "brcm/") and "/" not in source,
                     "invalid Wi-Fi source mapping")
        return profile
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        raise BootInputError("invalid cleanroom profile") from error


def validate_host(profile, *, product_type, device_class, board_id, chip_id):
    observed = (product_type, device_class, board_id, chip_id)
    expected = tuple(profile[key] for key in (
        "product_type", "device_class", "board_id", "chip_id"
    ))
    _require(observed == expected, "host does not match cleanroom model profile")


def apple_boot_identity(lock, host):
    """Match real hardware against identities extracted from the pinned IPSW."""
    identities = lock.get("boot_identities")
    _require(isinstance(identities, list) and 1 <= len(identities) <= 256,
             "missing Apple boot identities")
    products = lock.get("supported_products")
    _require(isinstance(products, list) and 1 <= len(products) <= 256
             and all(isinstance(p, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*,[0-9]+", p)
                     for p in products), "invalid Apple supported products")
    seen = set()
    for identity in identities:
        _require(isinstance(identity, dict) and set(identity) == {
            "device_class", "board_id", "chip_id", "members"}, "invalid Apple boot identity fields")
        device = identity["device_class"]
        _require(isinstance(device, str) and re.fullmatch(r"j[0-9]+[a-z]*ap", device),
                 "invalid Apple boot device class")
        for key in ("board_id", "chip_id"):
            _require(type(identity[key]) is int and 0 < identity[key] < 65536,
                     "invalid Apple boot " + key)
        key = (device, identity["board_id"], identity["chip_id"])
        _require(key not in seen, "duplicate Apple boot identity")
        seen.add(key)
        members = identity["members"]
        _require(isinstance(members, list) and members
                 and all(isinstance(name, str) and name in lock["members"] for name in members)
                 and len(members) == len(set(members)),
                 "Apple boot identity has unpinned members")
    if host is None:
        return None
    matches = [identity for identity in identities
               if all(identity[key] == getattr(host, key, None)
                      for key in ("device_class", "board_id", "chip_id"))]
    _require(getattr(host, "product_type", None) in products and len(matches) == 1,
             "This Apple restore build has no pinned boot identity for the actual Mac")
    return matches[0]


def maximum_apple_selection_bytes(lock, profile):
    """Reserve the native fallback or one YOLO host's boot-only selection."""
    from types import SimpleNamespace
    native = apple_boot_identity(lock, SimpleNamespace(**profile))
    selections = [set(native["members"]) | {lock["system_image"]["member"]}]
    selections.extend(set(identity["members"]) for identity in lock["boot_identities"])
    return max(sum(lock["members"][name]["size_bytes"] for name in selection)
               for selection in selections)


def select_apple_boot_profile(profile, lock, host):
    apple_boot_identity(lock, host)
    return {**profile, "device_identifier": "apple," + host.device_class[:-2],
            **{key: getattr(host, key) for key in ("product_type", "device_class", "board_id", "chip_id")}}


def ipsw_boot_profiles(archive, profile):
    """Build-time discovery for every physical Mac identity in one Apple IPSW."""
    manifest = plistlib.loads(archive.read(_member(archive, "BuildManifest.plist", 32 * 1024**2)))
    firmware = profile["firmware"]
    _require(manifest["ProductVersion"] == firmware["version"]
             and manifest["ProductBuildVersion"] == firmware["build"], "IPSW build differs from profile")
    profiles = []
    for identity in manifest["BuildIdentities"]:
        info = identity["Info"]
        if (not re.fullmatch(r"j[0-9]+[a-z]*ap", info["DeviceClass"])
                or info["Variant"] != firmware["variant"]
                or info["RestoreBehavior"] != firmware["restore_behavior"]):
            continue
        profiles.append({**profile, "device_identifier": "apple," + info["DeviceClass"][:-2],
                         "product_type": manifest["SupportedProductTypes"][0],
                         "device_class": info["DeviceClass"], "board_id": int(identity["ApBoardID"], 0),
                         "chip_id": int(identity["ApChipID"], 0)})
    _require(profiles, "IPSW contains no physical Mac boot identities")
    return manifest["SupportedProductTypes"], profiles


def _path(value):
    _require(isinstance(value, str) and value and "\\" not in value
             and all(ord(char) >= 32 and ord(char) != 127 for char in value),
             "invalid IPSW member path")
    path = PurePosixPath(value)
    _require(not path.is_absolute() and ".." not in path.parts
             and str(path) == value, "unsafe IPSW member path")
    return value


def _member(archive, name, maximum_size=None):
    name = _path(name)
    matches = [item for item in archive.infolist() if item.filename == name]
    _require(len(matches) == 1, "missing or duplicate IPSW member: " + name)
    item = matches[0]
    mode = item.external_attr >> 16
    _require(not item.is_dir() and not stat.S_ISLNK(mode)
             and (stat.S_IFMT(mode) in (0, stat.S_IFREG)),
             "IPSW member is not a regular file: " + name)
    _require(item.file_size > 0 and (maximum_size is None
             or item.file_size <= maximum_size), "invalid IPSW member size: " + name)
    return item


def inspect_ipsw(archive, profile):
    """Select one exact identity and preserve its Recovery authentication set.

    The caller must first authenticate either the enclosing IPSW against its
    admitted artifact descriptor or every selected member against signed pins. This checks structure/coherence, not Apple's signature.
    Encrypted BaseSystem input is reported explicitly, never renamed to a DMG.
    """
    try:
        manifest_info = _member(archive, "BuildManifest.plist", 32 * 1024 * 1024)
        version_info = _member(archive, "SystemVersion.plist", 1024 * 1024)
        manifest_bytes = archive.read(manifest_info)
        manifest = plistlib.loads(manifest_bytes)
        version = plistlib.loads(archive.read(version_info))
        firmware = profile["firmware"]
        for value in (manifest, version):
            _require(value["ProductVersion"] == firmware["version"]
                     and value["ProductBuildVersion"] == firmware["build"],
                     "IPSW firmware version does not match profile")
        _require(profile["product_type"] in manifest["SupportedProductTypes"],
                 "IPSW does not support product")
        matches = []
        for candidate in manifest["BuildIdentities"]:
            info = candidate["Info"]
            if (int(candidate["ApBoardID"], 0) == profile["board_id"]
                    and int(candidate["ApChipID"], 0) == profile["chip_id"]
                    and info["DeviceClass"] == profile["device_class"]
                    and info["Variant"] == firmware["variant"]
                    and info["RestoreBehavior"] == firmware["restore_behavior"]):
                matches.append(candidate)
        _require(len(matches) == 1, "expected one exact IPSW build identity")
        identity = matches[0]
        _require(identity["Info"]["BuildNumber"] == firmware["build"],
                 "identity build differs from IPSW")
        components = identity["Manifest"]
        paths = {key: _path(value["Info"]["Path"])
                 for key, value in components.items()}
        base = paths["BaseSystem"]
        _require(paths["BaseSystemVolume"] == "Firmware/" + base + ".root_hash"
                 and paths["Ap,BaseSystemTrustCache"] == "Firmware/" + base + ".trustcache",
                 "Recovery authentication references do not match BaseSystem")
        firmware_paths = sorted({path for path in paths.values()
                                 if path.startswith("Firmware/")})
        for path in firmware_paths:
            _member(archive, path)
        base_info = _member(archive, base)
        with archive.open(base_info) as stream:
            encrypted = stream.read(4) == b"AEA1"
        _require(base.endswith(".dmg.aea") if encrypted else base.endswith(".dmg"),
                 "BaseSystem format differs from its manifest path")
        narrowed = copy.deepcopy(manifest)
        narrowed["BuildIdentities"] = [copy.deepcopy(identity)]
        return {
            "manifest": narrowed,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "firmware_paths": firmware_paths,
            "recovery": {
                "image": base,
                "size_bytes": base_info.file_size,
                "format": "aea" if encrypted else "dmg",
                "root_hash": paths["BaseSystemVolume"],
                "trustcache": paths["Ap,BaseSystemTrustCache"],
            },
        }
    except (KeyError, TypeError, ValueError, plistlib.InvalidFileException) as error:
        if isinstance(error, BootInputError):
            raise
        raise BootInputError("invalid IPSW metadata") from error


def stub_members(archive, profile):
    """Validate the full fresh-stub closure before an allocation can begin."""
    selection = inspect_ipsw(archive, profile)
    required = {
        "BuildManifest.plist", "SystemVersion.plist", "RestoreVersion.plist",
        "PlatformSupport.plist", "usr/standalone/bootcaches.plist",
        "BootabilityBundle/Restore/Firmware/Bootability.dmg.trustcache",
        selection["recovery"]["image"], *selection["firmware_paths"],
    }
    components = selection["manifest"]["BuildIdentities"][0]["Manifest"]
    for key, value in components.items():
        if key not in {"BaseSystem", "OS", "Ap,SystemVolumeCanonicalMetadata",
                       "RestoreRamDisk", "RestoreTrustCache"} and not key.startswith("Cryptex"):
            required.add(value["Info"]["Path"])
    bootability = "BootabilityBundle/Restore/Bootability/"
    framework = bootability + "BootabilityBrain.framework/"
    required.update({
        framework + "Versions/A/BootabilityBrain",
        framework + "Versions/A/Resources/Info.plist",
        framework + "Versions/A/_CodeSignature/CodeResources",
        bootability + "System/Library/CoreServices/RestoreVersion.plist",
    })
    tickets = "Firmware/Manifests/restore/" + profile["firmware"]["variant"] + "/"
    required.add(tickets + "apticket." + profile["device_class"] + ".im4m")
    for name in required:
        _member(archive, name)
    trees = [item for item in archive.infolist()
             if item.filename.startswith((bootability, tickets))]
    names = set(required)
    seen = set()
    links = {}
    for item in trees:
        _path(item.filename.rstrip("/") if item.is_dir() else item.filename)
        _require(item.filename not in seen, "duplicate Apple restore tree entry")
        seen.add(item.filename)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            _require(item.filename.startswith(bootability) and item.file_size <= 1024,
                     "invalid Apple restore symlink")
            target = _path(archive.read(item).decode("utf-8"))
            resolved = str(PurePosixPath(item.filename).parent / target)
            links[item.filename] = resolved
        else:
            _require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                     "invalid Apple restore tree member type")
        names.add(item.filename)
    tree_names = {name.rstrip("/") for name in seen}
    for resolved in links.values():
        for _ in range(len(links) + 1):
            parts = PurePosixPath(resolved).parts
            for index in range(1, len(parts) + 1):
                prefix = "/".join(parts[:index])
                if prefix in links:
                    resolved = str(PurePosixPath(links[prefix], *parts[index:]))
                    break
            else:
                break
        else:
            raise BootInputError("cyclic Apple restore symlink")
        _require(resolved in tree_names, "dangling Apple restore symlink")
    for name in seen:
        _require(not any(str(parent) in links for parent in PurePosixPath(name).parents),
                 "Apple restore tree traverses a symlink")
    _require({framework + "Versions/Current", framework + "BootabilityBrain",
              framework + "Resources"}.issubset(links), "incomplete Bootability framework")
    return sorted(names)


def assemble_stage1(base, *, expected_sha256, esp_uuid):
    """Bind an admitted RELEASE=1 CHAINLOADING=1 raw build to a new ESP.

    Its build provenance must establish those options. Content checks below
    additionally catch accidental stage-2 selection and already bound inputs.
    """
    _require(isinstance(base, bytes) and 2048 < len(base) <= 16 * 1024 * 1024,
             "invalid raw stage-1 image size")
    _require(_hex(expected_sha256, 64)
             and hashlib.sha256(base).hexdigest() == expected_sha256,
             "stage-1 input digest mismatch")
    _require(b"Chainloading files not supported in this build!" not in base,
             "stage-1 build does not support file chainloading")
    assignment = rb"(?:chosen\.asahi,efi-system-partition|chainload)=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    _require(re.search(assignment, base) is None, "stage-1 input is already bound")
    try:
        parsed_uuid = uuid.UUID(esp_uuid)
        _require(parsed_uuid.int != 0 and str(parsed_uuid) == esp_uuid.lower(),
                 "invalid ESP UUID")
        identifier = str(parsed_uuid).upper()
    except (ValueError, TypeError, AttributeError) as error:
        raise BootInputError("invalid ESP UUID") from error
    variables = (
        f"chosen.asahi,efi-system-partition={identifier}\n"
        f"chainload={identifier};m1n1/boot.bin\n"
    ).encode("ascii")
    result = base + variables + b"\0" * 4
    return result + b"\0" * (-len(result) % 16384)
