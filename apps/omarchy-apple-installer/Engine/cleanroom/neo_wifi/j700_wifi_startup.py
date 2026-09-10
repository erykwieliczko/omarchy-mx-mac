# Source: linux-enablement-mac-alpha eb18361654bef156f170f1a70530227757056933
#!/usr/bin/env python3
"""Offline typed startup serializers; NOT a complete or executable RF program.

Contracts: NEO_TYPED_PRECAL_DEFAULTS_AND_OCA2_TYPE01. OCA source order is the
raw-input alternative, not equivalence with the transcript-based fullmac path.
"""
import struct
import hashlib
import csv
import re
from .j700_wifi_artifacts import calibration_directory


# Numeric original HOST locale data, build25E252/173. Behavioral handoff:
# NEO_ALL_COUNTRY_WORLD_POWER_PASSIVE_CONTRACT; all89 entries. This is a
# selector table, NOT a country whitelist: absent locales omit the third SET.
LOCALE_TYPES = {
    country: kind for kind, countries in (
        (1, 'US CA MX SV CO PR CR EC AR VI GT BR UM'),
        (2, 'AT BE BG CY CZ DK EE FR FI DE GR HU IS IT IE LV LI LT LU MT NL NO PL PT RO SK SI ES SE CH GB ZA TR AE SA HR RS XK'),
        (3, 'JP'), (4, 'KR'),
        (5, 'AU HK NZ SG MY VN BN TH KH LA MM CN'),
        (6, 'TW PK NP BD CL PA VE UY LK MV AF MN BT MO PH PE DO GU FJ NC PG WS VU'),
        (7, 'IN'),
    ) for country in countries.split()
}


def country_sar_mode(database, country):
    """Select original country/locale TaSAR branch, never infer from rates."""
    if not re.fullmatch('[A-Z0-9]{2}', country):
        raise ValueError('invalid original country identifier')
    if hashlib.sha256(database).hexdigest() != 'a61e0b70cbc9a381fc41ff9afa8f036cbd032cadad66209134d4a5adfa18c8b2':
        raise ValueError('unqualified country database')
    active, found = False, None
    for line in database.decode('ascii').splitlines():
        if line.startswith('<2gCC,'):
            columns = [s.strip() for s in next(csv.reader([line[1:-1]]))]
            if len(columns) != 77 or columns[-1] != 'dsa':
                raise ValueError('unexpected country table geometry')
            active = True
        elif active and line.startswith('</'):
            active = False
        elif active and line.startswith(country + ','):
            row = [s.strip() for s in next(csv.reader([line]))]
            if found is not None or len(row) != 78 or row[-1]:
                raise ValueError('invalid/duplicate country row')
            found = int(row[76], 8)
    if found is None:
        raise ValueError('country absent from original database; no fallback')
    if found != 1:
        commands = [b'SarEnableConfig 1', b'coex tasar_set 0 0']
    else:
        commands = [b'SarEnableConfig 0', b'coex tasar_set 0 1']
        kind = LOCALE_TYPES.get(country)
        if kind is not None:
            selector = 3 if kind == 4 else 2 if kind == 2 else int(kind == 1 and country == 'CA')
            commands.append(f'coex tasar_set 13 {selector}'.encode('ascii'))
    return [text_command(text) for text in commands]


def pl_sar_mode(database):
    """Compatibility wrapper for the original PL serializer fixtures."""
    return country_sar_mode(database, 'PL')


def original_config(data, phy=None):
    """Original ordered table, optionally reconciled with actual PHY tag8."""
    if hashlib.sha256(data).hexdigest() != '9f1adcb329c47a25421cad3ede473e1fdf41edb36f295f17f021d2689430899d':
        raise ValueError('unqualified original configuration input')
    entries = {'AdapScan': '0x0'}
    for raw in data.decode('ascii').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 2 or fields[0] in entries:
            raise ValueError('malformed/duplicate original config entry')
        if len(fields) > 2 and fields[0] != 'dbgLevel0':
            raise ValueError('unqualified multi-token config')
        entries[fields[0]] = ' '.join(fields[1:]) + (' ' if len(fields) > 2 else '')
    if len(entries) != 65:
        raise ValueError('unexpected original entry count')
    if phy is not None:
        if len(phy) != 12 or not 1 <= phy[4] <= 4:
            raise ValueError('invalid actual PHY capability')
        if not phy[5]:
            entries['DbdcMode'] = '0x0'
        updates = (('StaVHT',1,None),('ApVHT',1,1),('P2pGoVHT',1,1),('P2pGcVHT',1,1),
                   ('Nss',4,None),('LdpcTx',6,None),('LdpcRx',7,None),('StbcTx',8,0),('StbcRx',9,0))
        for key, at, default in updates:
            requested = int(entries[key], 0) if key in entries else default
            if requested is None or not 0 <= requested <= 255:
                raise ValueError('invalid requested PHY feature')
            entries[key] = hex(min(requested, phy[at]) if key == 'Nss' else requested & phy[at])
    return list(entries.items())


def config_records(records):
    if not 1 <= len(records) <= 4:
        raise ValueError('captured CID70 variant requires 1..4 records')
    out = bytearray(284)
    struct.pack_into('<IIBBH', out, 0, 0, 1, len(records), 0, len(records) * 68)
    for i, (key, value) in enumerate(records):
        key, value = key.encode('ascii'), value.encode('ascii')
        if not 0 < len(key) <= 32 or not len(value) <= 32 or b'\0' in key:
            raise ValueError('invalid configuration string extent')
        at = 12 + 68 * i
        out[at:at + 4] = bytes((3, len(key), len(value), 0))
        out[at + 4:at + 4 + len(key)] = key
        out[at + 36:at + 36 + len(value)] = value
    return bytes(out)


def text_command(text):
    if not isinstance(text, bytes) or not 0 < len(text) <= 320:
        raise ValueError('CIDca requires 1..320 original text bytes')
    return struct.pack('<II', 0, len(text)) + text + bytes(320 - len(text))


def power_on_type01(oca2, has_6ghz, *, preload_version):
    if type(has_6ghz) is not bool:
        raise ValueError('explicit firmware capability required')
    if type(preload_version) is not int or not 0 <= preload_version <= 255:
        raise ValueError('explicit BF-negotiated preload version required')
    entries = calibration_directory(oca2)
    directory = {entry['tag']: entry for entry in entries}
    if len(directory) != len(entries):
        raise ValueError('duplicate calibration tag')
    result = []

    def emit(kind, parameter, tag, part, count, source, data):
        if not 0 < len(data) <= 1080 or not 0 <= part < count <= 15:
            raise ValueError('invalid source-profile fragment')
        fragment = count << 4 | part
        context = 0xff if preload_version else 0
        header = struct.pack('<BBBBII8x', kind, context, fragment, 1, len(data), parameter)
        result.append(dict(type=kind, parameter=parameter, tag=tag,
                           fragment=fragment, source_offset=source,
                           body=header + data))

    def segment(tag):
        if tag not in directory:
            raise ValueError(f'missing required OCA2 tag {tag:04x}')
        entry = directory[tag]
        start = entry['offset'] + 8
        return start, oca2[start:entry['offset'] + entry['length']]

    groups = ((0x1001, 0x1002, 0x1003), (0x1011, 0x1012))
    if has_6ghz:
        groups += ((0x1021, 0x1022),)
    for group, tags in enumerate(groups):
        for part, tag in enumerate(tags):
            start, data = segment(tag)
            emit(0, group, tag, part, len(tags), start, data)
    for parameter in (0, 0x10000, 8) + ((0x10,) if has_6ghz else ()):
        tag = 0x2000 | (parameter & 0x7f) | (0x80 if parameter & 0x10000 else 0)
        start, data = segment(tag)
        count = 1 if tag & 0x80 else 2
        if len(data) % count:
            raise ValueError('per-band OCA2 payload is not evenly divisible')
        width = len(data) // count
        for part in range(count):
            emit(1, parameter, tag, part, count, start + part * width,
                 data[part * width:(part + 1) * width])
    return result


def calibration_request_2g(oca2, payload, *, smart_version, preload_version, module_byte):
    """Service a genuine D7 payload, bounded to the initial PL 2G design.

    No request is synthesized here. NEO_HOST_D7_CAL_REQUEST_AND_TYPE23_ORDER
    defines source selection, dependencies, remap and metadata slicing.
    """
    if len(payload) != 16:
        raise ValueError('D7 requires exactly16 payload bytes')
    _, upper, band, channel = struct.unpack('<4I', payload)
    if band != 0 or not 1 <= channel <= 13 or upper & ~0x1001:
        raise ValueError('unqualified D7 band/channel/action context')
    if type(smart_version) is not int or not 0 <= smart_version <= 12:
        raise ValueError('unknown negotiated SmartVersion')
    if type(preload_version) is not int or not 0 <= preload_version <= 255:
        raise ValueError('unknown negotiated preload version')
    if type(module_byte) is not int or not 0 <= module_byte <= 255:
        raise ValueError('invalid eFuse module byte')
    directory = calibration_directory(oca2)
    entries = {entry['tag']: entry for entry in directory}
    if len(entries) != len(directory):
        raise ValueError('duplicate calibration tag')

    def segment(tag):
        entry = entries[tag]
        start = entry['offset'] + 8
        return start, oca2[start:entry['offset'] + entry['length']]

    rows = (bytes.fromhex('000f03'),) * 2 + (bytes.fromhex('310703'), bytes.fromhex('210703'))
    rows += (bytes(3),) * 7 + (bytes.fromhex('610f07'),) * 2
    rule = rows[smart_version]
    _, override = segment(0x501)
    if len(override) != 200:
        raise ValueError('invalid original0501 payload')
    disable = bytes(2)
    file_version = struct.unpack_from('>H', oca2, 8)[0]
    compatibility = smart_version >= 2 and file_version > smart_version and file_version >= 11 and smart_version < 11
    if override[1] and not compatibility:
        rule, disable = override[:3], override[3:5]
    width = int(channel in (3, 8, 12))
    if not rule[2] & 1 or not rule[1] & (1 << width):
        raise ValueError('channel unsupported by calibration rule')
    selected = channel
    if rule[0] & 16 and rule[0] & (1 << width) and not disable[(channel - 1) // 8] & (1 << ((channel - 1) % 8)):
        selected = 3 if channel <= 5 else 8 if channel <= 10 else 12
    parameter = (upper << 16) | channel
    action = parameter & 0x0e000000
    q = parameter if action >> 25 == 3 else parameter & 0xf1ffffff
    requests = ((1, (q & 0x7e000000) | (parameter & 0x10000)),
                (3, q & 0x7e00ffff),
                (2, (q & 0x7fff0fff) ^ 0x10000), (2, q | action))
    result = []
    for kind, param in requests:
        if kind == 1:
            tag = 0x2000 | (0x80 if param & 0x10000 else 0)
            start, raw = segment(tag)
            count = 1 if tag & 0x80 else 2
            if len(raw) % count:
                raise ValueError('invalid per-band data geometry')
            stride, metadata_size = len(raw) // count, 0
        elif kind == 3:
            tag = 0x4000 | channel
            start, raw = segment(tag)
            if len(raw) != 20:
                raise ValueError('invalid type3 segment')
            count, stride, metadata_size = 1, 20, 4
        else:
            param = (param | (channel << 17)) & ~0xfff | selected
            special = bool(param & 0x10000)
            tag = 0x3000 | selected | (0x80 if special else 0)
            start, raw = segment(tag)
            raw_count = (4 if module_byte & 15 == 1 else 8) if module_byte & 0x80 else 2
            raw_count //= 2 if special else 1
            if len(raw) != raw_count * 612:
                raise ValueError('eFuse count disagrees with original type2 segment')
            count = (1 if special else 3) if module_byte & 15 else raw_count
            if count > raw_count:
                raise ValueError('not enough original type2 records')
            stride, metadata_size = 612, 8
        for part in range(count):
            record = raw[part * stride:(part + 1) * stride]
            metadata = record[:metadata_size] + bytes(8 - metadata_size)
            data = record[metadata_size:]
            if not 0 < len(data) <= 1080 or count > 15:
                raise ValueError('invalid calibration fragment geometry')
            header = struct.pack('<BBBBII', kind, 0xff if preload_version else 0,
                                 count << 4 | part, 1, len(data), param)
            result.append(dict(type=kind, parameter=param, tag=tag,
                               source_offset=start + part * stride + metadata_size,
                               body=header + metadata + data))
    return result
