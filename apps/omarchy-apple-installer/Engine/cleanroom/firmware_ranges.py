# SPDX-License-Identifier: MIT
"""Bind native range extraction to the signed model and Apple input lock."""
import hashlib
import json
from pathlib import Path
import subprocess

from apple_inputs import progress
from boot_inputs import BootInputError


class FirmwareRangeFallback(BootInputError):
    """Remote range/extraction failure eligible for the fully verified path."""


def load_recipe(lock, profile, profile_directory):
    descriptor = lock.get("firmware_ranges")
    if (not isinstance(descriptor, dict)
            or set(descriptor) != {"file_name", "size_bytes", "sha256"}
            or not isinstance(descriptor["file_name"], str)
            or Path(descriptor["file_name"]).name != descriptor["file_name"]
            or not descriptor["file_name"].endswith(".json")
            or type(descriptor["size_bytes"]) is not int
            or not 0 < descriptor["size_bytes"] <= 1024 * 1024):
        raise BootInputError("invalid firmware range recipe descriptor")
    path = Path(profile_directory) / descriptor["file_name"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size != descriptor["size_bytes"]:
        raise BootInputError("firmware range recipe file differs from signed metadata")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise BootInputError("firmware range recipe SHA-256 differs from signed metadata")
    try:
        recipe = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise BootInputError("invalid firmware range recipe JSON") from error
    if not isinstance(recipe, dict):
        raise BootInputError("invalid firmware range recipe object")
    if (recipe.get("schema") != 1 or recipe.get("source") != lock["ipsw"]
            or recipe.get("system_image") != lock["system_image"]):
        raise BootInputError("firmware range recipe differs from admitted Apple build")
    files = recipe.get("files")
    if not isinstance(files, list) or len(files) != len(profile["wifi"]["files"]):
        raise BootInputError("firmware range output inventory differs from model")
    observed = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise BootInputError("invalid firmware range file descriptor")
        name = item.get("name")
        source = profile["wifi"]["files"].get(name)
        if (source is None or name in observed
                or item.get("source") != profile["wifi"]["source_directory"] + "/" + source
                or item.get("transform") != ("nvram" if source.endswith(".txt") else "identity")
                or item.get("sha256") != lock["linux_firmware"][name]):
            raise BootInputError("firmware range file differs from model source or final hash")
        observed[name] = item["sha256"]
    return path


def extract_wifi(lock, profile, profile_directory, decoder, destination, *, run=subprocess.run):
    # A damaged local recipe is never treated as a reason to bypass admission.
    recipe = load_recipe(lock, profile, profile_directory)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BootInputError("refusing to replace prepared range firmware")
    progress("Collecting Wi-Fi firmware using the signed Apple range recipe")
    try:
        run([str(decoder), "firmware-ranges", str(recipe), lock["firmware_ranges"]["sha256"],
             str(destination)], check=True)
    except subprocess.CalledProcessError as error:
        if error.returncode == 75:
            raise FirmwareRangeFallback("authenticated range extraction was unavailable; see decoder log") from error
        raise BootInputError("native firmware range decoder failed or was cancelled; see decoder log") from error
    # Keep the same FWFile representation and independent Python hash gate used
    # by the existing touchpad + Wi-Fi inventory admission.
    from asahi_firmware.core import FWFile
    from firmware import regular_bytes
    result = []
    if destination.is_symlink() or not destination.is_dir():
        raise BootInputError("native range decoder did not publish a firmware directory")
    expected = set(profile["wifi"]["files"])
    actual = set()
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise BootInputError("native range firmware contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(destination).as_posix())
    if actual != expected:
        raise BootInputError("native range firmware inventory mismatch")
    for name in sorted(expected):
        data = regular_bytes(destination / name)
        if hashlib.sha256(data).hexdigest() != lock["linux_firmware"][name]:
            raise BootInputError("range firmware differs from admitted Apple inputs")
        result.append((name, FWFile(profile["wifi"]["files"][name], data)))
    progress("All range-extracted Wi-Fi firmware hashes verified")
    return result
