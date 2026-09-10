# Source: linux-enablement-mac-alpha eb18361654bef156f170f1a70530227757056933
#!/usr/bin/env python3
"""Extract unchanged J700 firmware and same-device calibration, never executable code.

Input is an Apple macOS system filesystem and a full IOService ioreg plist from
the target. This does NOT convert OCA2 into a firmware command transcript.
Formats: NEO_MT7932_STOCK_ARTIFACTS_AND_CAL_CONVERSION cleanroom contract.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct

BUNDLE = Path('System/Library/DriverExtensions/com.apple.AppleSunriseWLAN.dext')
FILES = {
    'IZUBA_W7932_2.bin': 'WIFI_RAM_CODE_MT7932_1.bin',
    'IZUBA_WIFI_MT7932_patch_mcu_1_2_hdr.bin': 'WIFI_MT7932_patch_mcu_1_2_hdr.bin',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def span(data, offset, length, limit=None):
    end = len(data) if limit is None else limit
    require(0 <= offset <= end and 0 <= length <= end - offset, 'truncated/out-of-bounds span')
    return data[offset:offset + length]


def patch_regions(data):
    span(data, 0, 96)
    count = struct.unpack_from('>I', data, 44)[0]
    require(0 < count <= 4096, 'invalid patch region count')
    span(data, 96, count * 64)
    regions = []
    for index in range(count):
        kind, offset, size, target, length, key, align = struct.unpack_from(
            '>7I', data, 96 + index * 64)
        require(kind & 0xffff == 2, 'unsupported patch region type')
        require(offset >= 96 + count * 64 and 0 < length <= size,
                'patch data overlaps table or has invalid transfer length')
        span(data, offset, size)
        require(key == 0xffffffff or key >> 24 in (0, 1, 2), 'unsupported patch security type')
        regions.append(dict(offset=offset, size=size, target=target, length=length,
                            security=key, alignment=align))
    return regions


def ram_regions(data):
    require(len(data) >= 36, 'missing RAM trailer')
    trailer = data[-36:]
    count = trailer[2]
    require(count > 0 and count * 40 <= len(data) - 36, 'invalid RAM region count')
    metadata = len(data) - 36 - count * 40
    cursor = 0
    regions = []
    for index in range(count):
        at = metadata + index * 40
        target, length = struct.unpack_from('<II', data, at + 16)
        require(length > 0, 'empty RAM region')
        span(data, cursor, length, metadata)
        regions.append(dict(offset=cursor, target=target, length=length,
                            features=data[at + 24], type=data[at + 25]))
        cursor += length  # Includes non-download regions; never transmit the metadata gap.
    return dict(chip=trailer[0], eco=trailer[1], format=trailer[3], flags=trailer[4],
                version=trailer[7:17].rstrip(b'\0').decode('ascii', errors='replace'),
                date=trailer[17:32].rstrip(b'\0').decode('ascii', errors='replace'),
                regions=regions, metadata_start=metadata, data_end=cursor)


def calibration_directory(data):
    span(data, 0, 16)
    require(data[:4] == b'BLOB', 'missing calibration BLOB header')
    end, version, count = struct.unpack_from('>IHH', data, 4)
    require(version == 12 and count > 0 and end == 16 + 20 * count,
            'invalid calibration directory')
    span(data, 0, end)
    entries = []
    for index in range(count):
        tag, header, offset, length, checksum = struct.unpack_from('>HHIII', data, 16 + 20 * index)
        require(header == 12 and offset >= end and length >= 12, 'invalid calibration entry')
        segment = span(data, offset, length)
        st, sh, sl = struct.unpack_from('>HHI', segment)
        require((st, sh, sl) == (tag, 12, length), 'calibration segment/header mismatch')
        require(sum(segment) & 0xffffffff == checksum, 'calibration checksum mismatch')
        entries.append(dict(tag=tag, offset=offset, length=length))
    return entries


def properties(tree, name):
    if isinstance(tree, dict):
        if name in tree:
            yield tree[name]
        for value in tree.values():
            yield from properties(value, name)
    elif isinstance(tree, list):
        for value in tree:
            yield from properties(value, name)


def calibration_from_ioreg(tree, expected_serial):
    serials = set(properties(tree, 'IOPlatformSerialNumber'))
    require(serials == {expected_serial}, 'ioreg capture is not uniquely tied to the expected target')
    result = {}
    for suffix in ('wcal', 'oca2'):
        values = list(properties(tree, 'wifi-calibration-' + suffix))
        require(values and all(isinstance(v, bytes) for v in values), 'missing binary ' + suffix)
        require(all(v == values[0] for v in values), 'conflicting calibration properties: ' + suffix)
        result[suffix] = values[0]
    require(0 < len(result['wcal']) <= 1024, 'WCAL length is outside the loader contract')
    calibration_directory(result['oca2'])
    return result


def extract(system_root, capture, serial, output):
    bundle = system_root / BUNDLE
    data = {dest: (bundle / 'IZUBA' / src).read_bytes() for src, dest in FILES.items()}
    layouts = {
        'patch': patch_regions(data['WIFI_MT7932_patch_mcu_1_2_hdr.bin']),
        'ram': ram_regions(data['WIFI_RAM_CODE_MT7932_1.bin']),
    }
    cal = calibration_from_ioreg(plistlib.loads(capture.read_bytes()), serial)
    data['j700-mt7932-wcal.bin'] = cal['wcal']
    data['j700-mt7932-oca2.raw'] = cal['oca2']
    version = system_root / 'System/Library/CoreServices/SystemVersion.plist'
    manifest = dict(target_serial=serial, source_bundle=str(bundle),
                    macos=plistlib.loads(version.read_bytes()), layouts=layouts,
                    calibration_note='Raw OCA2 is NOT a fullmac one-time-cal command stream.',
                    files={name: dict(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
                           for name, blob in data.items()})
    for name in ('version.plist', 'Info.plist'):
        member = bundle / name
        if member.is_file():
            # Retain original metadata unchanged, rather than inventing an OS/build relationship.
            data['bundle-' + name] = member.read_bytes()
    # Complete validation before any output, never overwrite an existing extraction.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, blob in data.items():
        with (output / name).open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(blob)
    with (output / 'manifest.json').open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system-root', type=Path, required=True)
    parser.add_argument('--ioreg', type=Path, required=True,
                        help='Full IOService plist, including IOPlatformSerialNumber and WLAN data')
    parser.add_argument('--expect-serial', required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; parent must exist')
    args = parser.parse_args()
    manifest = extract(args.system_root, args.ioreg, args.expect_serial, args.output)
    for name, info in manifest['files'].items():
        print(name, info['bytes'], info['sha256'])
    print('Opaque extraction only; raw OCA2 is not ready for the firmware command loader.')


if __name__ == '__main__':
    main()
