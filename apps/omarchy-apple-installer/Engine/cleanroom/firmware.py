# SPDX-License-Identifier: MIT
"""Extract generic model firmware from the required full macOS baseline."""
import os
from pathlib import Path
import plistlib
import stat

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
        data = regular_bytes(path)
        if name.endswith('.txt'):
            data = normalize_nvram(data)
        result.append((destination, FWFile(name, data)))
    return result
