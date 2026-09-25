# SPDX-License-Identifier: MIT
"""Extract generic model firmware from the required full macOS baseline."""
import os
from pathlib import Path
import plistlib
import stat
import hashlib
import io
import subprocess
import tarfile

from asahi_firmware.core import FWFile
from boot_inputs import BootInputError


def regular_bytes(path, maximum=4 * 1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as reader:
        status = os.fstat(reader.fileno())
        if not stat.S_ISREG(status.st_mode) or not 0 < status.st_size <= maximum:
            raise BootInputError('invalid macOS firmware input')
        data = reader.read(maximum + 1)
        if len(data) != status.st_size:
            raise BootInputError('macOS firmware input changed')
        return data


def normalize_nvram(data):
    # Match the established Linux brcmfmac NVRAM conversion: strip whitespace
    # around keys, retain values and order, and terminate each record with LF.
    lines = []
    for line in data.decode('ascii').split('\n'):
        if line:
            key, value = line.split('=', 1)
            lines.append(f'{key.strip()}={value}\n')
    return ''.join(lines).encode('ascii')


def collect_macos_wifi(profile, system_root):
    system_root = Path(system_root).resolve(strict=True)
    version = plistlib.loads(regular_bytes(
        system_root / 'System/Library/CoreServices/SystemVersion.plist', 65536))
    if (version.get('ProductVersion') != profile['firmware']['version']
            or version.get('ProductBuildVersion') != profile['firmware']['build']):
        raise BootInputError('full macOS firmware baseline differs from profile')
    source = (system_root / profile['wifi']['source_directory']).resolve(strict=True)
    if not source.is_relative_to(system_root):
        raise BootInputError('macOS firmware directory escapes the downloaded system image')
    result = []
    for destination, name in profile['wifi']['files'].items():
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source):
            raise BootInputError('macOS firmware link escapes its model directory')
        data = regular_bytes(path, 32 * 1024 * 1024 if profile["device_identifier"] == "apple,j700" else 4 * 1024 * 1024)
        if name.endswith('.txt'):
            data = normalize_nvram(data)
        result.append((destination, FWFile(name, data)))
    return result


def convert_neo_wifi(files, lock, workspace):
    """Derive Linux policy from authenticated Apple originals on the target."""
    from neo_wifi.j700_wifi_boot_policy import generate_all, firmware_tree
    originals = dict(files)
    expected = lock["wifi_source_hashes"]
    if set(originals) != set(expected):
        raise BootInputError("incomplete Neo Wi-Fi originals")
    for name, value in originals.items():
        if hashlib.sha256(value.data).hexdigest() != expected[name]:
            raise BootInputError("Neo Wi-Fi original differs from Apple input lock")
    workspace = Path(workspace)
    workspace.mkdir()
    archive = workspace / "policy-inputs.tar.gz"
    with tarfile.open(archive, "w:gz") as writer:
        for name, value in originals.items():
            info = tarfile.TarInfo(Path(name).name)
            info.size = len(value.data)
            writer.addfile(info, io.BytesIO(value.data))
    database = workspace / "database.dat"
    database.write_bytes(originals["sunrise/IZUBA_db.dat"].data)
    policies = workspace / "policies"
    generate_all(archive, policies, database, jobs=4)
    output = workspace / "firmware"
    firmware_tree(policies, output)
    result = [(path.relative_to(output).as_posix(), FWFile(path.name, regular_bytes(path)))
              for path in sorted(output.rglob("*.bin"))]
    for source, destination in (
        ("IZUBA_W7932_2.bin", "IZUBA_W7932_2.bin"),
        ("IZUBA_WIFI_MT7932_patch_mcu_1_2_hdr.bin", "IZUBA_WIFI_MT7932_patch_mcu_1_2_hdr.bin"),
        ("IZUBA_PPR_MT7932.bin", "ppr.bin"),
    ):
        result.append(("mediatek/mt7932/" + destination, originals["sunrise/" + source]))
    return result


def convert_neo_touchpad(data):
    from asahi_firmware.img4 import img4p_extract
    from asahi_firmware.multitouch import load_plist_xml, plist_to_bin_trackpad
    name, payload = img4p_extract(data)
    if name != "mtfw":
        raise BootInputError("unexpected Neo touchpad Image4 payload")
    entries = load_plist_xml(payload.rstrip(b"\x00"))
    if set(entries) != {"C1FE0,0"}:
        raise BootInputError("unexpected Neo touchpad firmware inventory")
    return [("apple/tpmtfw-j700.bin", FWFile("Multitouch.im4p", plist_to_bin_trackpad(entries["C1FE0,0"])))]


def collect_neo_calibration(*, run=subprocess.check_output):
    """Read calibration from this physical Mac, never a model-wide fallback."""
    from neo_wifi.j700_wifi_artifacts import calibration_from_ioreg, properties
    expert = plistlib.loads(run(["/usr/sbin/ioreg", "-a", "-r", "-c", "IOPlatformExpertDevice"]))
    serials = list(properties(expert, "IOPlatformSerialNumber"))
    if len(serials) != 1 or not isinstance(serials[0], str) or not serials[0]:
        raise BootInputError("cannot bind Neo Wi-Fi calibration to this Mac")
    tree = plistlib.loads(run(["/usr/sbin/ioreg", "-a", "-l", "-p", "IOService"]))
    try:
        calibration = calibration_from_ioreg(tree, serials[0])
    except ValueError as error:
        raise BootInputError("invalid target Neo Wi-Fi calibration: " + str(error)) from error
    return [("mediatek/mt7932/" + name + ".bin", FWFile(name, data))
            for name, data in sorted(calibration.items())]
