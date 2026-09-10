# Source: linux-enablement-mac-alpha eb18361654bef156f170f1a70530227757056933
#!/usr/bin/env python3
"""Host-format3 intermediate policy model, not a regulatory channel allowlist.

Consumes fully expanded, country-selected records. The input-file contextual
loader is separate; no XZ fallback or firmware transmission occurs here.
Contracts: NEO_HOST_1SS1T_BITMAP_CONTRACT, NEO_HOST_TYPE5_FULL_CONTRACT.
"""
import re
import struct
from .j700_wifi_policy import normal_2g_records, parse_tables, RATE_CATEGORIES, RATE_WIDTHS

# Numeric host relation data: primary20, center40, center80, center160.
RELATIONS = [(ch, 0, 0, 0) for ch in range(1, 15)] + [
    (36,38,42,50),(40,38,42,50),(44,46,42,50),(48,46,42,50),
    (52,54,58,50),(56,54,58,50),(60,62,58,50),(64,62,58,50),
    (100,102,106,114),(104,102,106,114),(108,110,106,114),(112,110,106,114),
    (116,118,122,114),(120,118,122,114),(124,126,122,114),(128,126,122,114),
    (132,134,138,0),(136,134,138,0),(140,142,138,0),(144,142,138,0),
    (149,151,155,163),(153,151,155,163),(157,159,155,163),(161,159,155,163),
    (165,167,171,163),(169,167,171,163),(173,175,171,163),(177,175,171,163),
    (181,0,0,0),
]
CHANNELS = tuple(sorted({ch for row in RELATIONS for ch in row if ch}))


def expand_original(data, country):
    """Expand format3 original rows in source order; never infer RF permission.

    Contextual routing follows NEO_HOST_CONTEXTUAL_RATE_LOADER_ADDENDUM.
    Select the explicit original country; never substitute a nearby country.
    """
    if b'<Ver:03>' not in data or not re.fullmatch('[A-Z]{2}', country):
        raise ValueError('unqualified original rate profile')
    tables = parse_tables(data)[country]
    records = {ch: [[-60] * 36 for _ in RATE_CATEGORIES] for ch in CHANNELS}

    def center(channel):
        for row in RELATIONS:
            for width in (1, 2, 3):
                if row[width] == channel:
                    return width
        return 0

    for category, (name, width) in enumerate(zip(RATE_CATEGORIES, RATE_WIDTHS)):
        table = tables.get(name)
        if table is None or len(table['columns']) != width:
            raise ValueError('missing category or incorrect width')
        for label, values in table['records'].items():
            match = re.fullmatch(r'ch([0-9]{3})(?:-([0-9]{3}))?(-cdd)?', label)
            if not match or len(values) != width:
                raise ValueError('invalid rate row')
            source = int(match[1])
            target = int(match[2]) if match[2] else source
            if source not in records or target not in records:
                raise ValueError('unknown channel record')
            if category in (2, 3):
                continue
            encoded = [-60 if v in ('X', 'x') else 127 if v == 'Y' else v for v in values]
            destination, start = category, 0
            contextual = bool(match[2] or match[3])
            if contextual:
                s, t = center(source), center(target)
                if not s:
                    raise ValueError('contextual source is not a center')
                if category in (4, 5, 6):
                    destination = 4 + s
                elif category in (11, 12, 13):
                    destination = 11 + s
                elif category == 1:
                    destination, start = 2, 8 * (s - 1)
                    if t == s:
                        start = 8
                    elif (s, t) in ((2, 1), (3, 2)):
                        destination, start = (3, 0) if match[3] else (2, 16)
                    elif (s, t) == (3, 1):
                        destination, start = 3, 16 if match[3] else 8
                    elif t:
                        raise ValueError('unqualified center combination')
            records[target][destination][start:start + width] = encoded
            if category == 1 and not contextual:
                records[target][2][:8] = encoded
    validate_expanded(records)
    return records


def bandwidth_index(channel):
    if type(channel) is not int or not 1 <= channel <= 181:
        raise ValueError('invalid non6GHz channel record')
    if channel <= 14:
        return 0
    delta = channel - (36 if channel < 149 else 149)
    if delta < 0:
        raise ValueError('invalid non6GHz channel record')
    for index, (modulus, residue) in enumerate(((4,0),(8,2),(16,6),(32,14))):
        if delta % modulus == residue:
            return index
    raise ValueError('invalid non6GHz channel width')


def validate_expanded(records):
    if set(records) != set(CHANNELS):
        raise ValueError('all67 contributing channel records required')
    for groups in records.values():
        if len(groups) != 15 or any(len(group) != 36 for group in groups):
            raise ValueError('expanded record must contain15 groups of36 values')
        if any(type(v) is not int or not -128 <= v <= 127 for group in groups for v in group):
            raise ValueError('expanded values must be signed bytes')


def one_stream_bitmap(records):
    validate_expanded(records)
    bitmap = bytearray(32)
    pairs = ((2,0,8),(3,0,8),(6,0,10),(7,0,10))
    for channel, groups in records.items():
        width = bandwidth_index(channel)
        if all(groups[g][first] - groups[g][second] >= 10 for g, first, second in pairs[:width + 1]):
            bitmap[channel // 8] |= 1 << (channel % 8)
    return bytes(bitmap)


def unsupported_rate_body(records):
    validate_expanded(records)
    relations = {r[0]: r for r in RELATIONS}
    words = [0] * 90

    def unsupported(vector):
        if 127 in vector:
            raise ValueError('unqualified7f in final checked vector')
        return -60 in vector

    def pair(destination, first, second, fallback):
        if first[0] == 127:
            first = fallback[0:8]
        if second[0] == 127:
            second = fallback[10:18]
        if len(first) != 8 or len(second) != 8:
            raise ValueError('invalid reconstructed vector extent')
        destination[0:8], destination[8:16] = first, second

    for channel in CHANNELS:
        groups = records[channel]
        width = bandwidth_index(channel)
        mask = 0
        for category in range(8, 12 + width):
            for vector in range(3):
                if unsupported(groups[category][12 * vector:12 * (vector + 1)]):
                    mask |= 0x30000000 if category == 8 else 0x10000000
                    if category >= 11 and category == width + 10:
                        mask |= 0x40000000
        scratch = [[0] * 36 for _ in range(15)]
        if width == 0:
            scratch[1][0:8] = groups[1][0:8]
            scratch[1][8:16] = groups[4][10:18]
            scratch[11] = list(groups[11])
        else:
            start = channel - (2, 6, 14)[width - 1]
            primary = next((ch for ch in range(start, start + 4 * (1 << width), 4)
                            if ch in records), None)
            if primary not in relations:
                raise ValueError('missing constituent primary record')
            p = records[primary]
            c40 = records[relations[primary][1]]
            scratch[1][0:8] = p[2][8 * (width - 1):8 * width]
            scratch[1][8:16] = p[4 + width][10:18]
            if width >= 2:
                scratch[11] = list(p[11 + width])
                first = c40[2][16:24] if width == 2 else c40[3][8:16]
                second = c40[3][0:8] if width == 2 else c40[3][16:24]
                pair(scratch[2], first, second, c40[4 + width])
            if width == 3:
                c80 = records[relations[primary][2]]
                scratch[12] = list(c40[14])
                pair(scratch[3], c80[2][16:24], c80[3][0:8], c80[7])
            pair(scratch[width + 1], groups[2][0:8], groups[2][8:16], groups[4 + width])
            scratch[10 + width] = list(groups[10 + width])
            scratch[11 + width] = list(groups[11 + width])
        for category in (1, 2, 3, 4, 11, 12, 13, 14):
            size = 8 if category <= 4 else 12
            for vector in range(3):
                if unsupported(scratch[category][size * vector:size * (vector + 1)]):
                    if category <= 4:
                        if vector < 2:
                            mask |= 1 << (3 * (category - 1) + vector)
                    else:
                        mask |= 1 << (3 * category - 21 + vector)
        if any(mask & (1 << (13 + 3 * i)) for i in range(width + 1)):
            mask |= 1 << 31
        index = channel if channel <= 14 else channel // 2 - 1
        words[index] |= mask
    body = bytearray(532)
    body[:8] = bytes.fromhex('0002140204020005')
    struct.pack_into('<90I', body, 44, *words)
    return bytes(body)


def normal_2g_bodies(original_policy, country, expanded):
    """Join original2G records with fully prepared non6GHz bitmap context."""
    records = normal_2g_records(original_policy, country, range(1, 15 if country == 'XZ' else 14))
    bitmap = one_stream_bitmap(expanded)
    out = []
    for start in range(0, len(records), 8):
        batch = records[start:start + 8]
        body = bytearray(44)
        struct.pack_into('<BBHBBBB', body, 0, 3, 0x0a, 44 + 122 * len(batch),
                         len(batch), 1, int(start + len(batch) == len(records)), 0)
        body[8:10] = country.encode('ascii')
        body[12:44] = bitmap
        out.append(bytes(body) + b''.join(batch))
    return out


def normal_5g_bodies(country, expanded):
    """Project original contextual records; center rows are policy, not permissions."""
    validate_expanded(expanded)
    if not re.fullmatch('[A-Z]{2}', country):
        raise ValueError('invalid country')
    records = []
    for channel in CHANNELS:
        if channel <= 14:
            continue
        record = bytearray((channel,))
        for category, (name, width) in enumerate(zip(RATE_CATEGORIES, RATE_WIDTHS)):
            if name == 'cck':
                indices = (0,)
            elif name == 'ofdm':
                indices = (0, 4, 6)
            else:
                within = (1, 5, 7) if name.startswith('ht') else (0, 3, 5)
                indices = tuple(group * (width // 3) + at for group in range(3) for at in within)
            record.extend(expanded[channel][category][at] & 255 for at in indices)
        assert len(record) == 122
        records.append(bytes(record))
    bitmap = one_stream_bitmap(expanded)
    out = []
    for start in range(0, len(records), 8):
        batch = records[start:start + 8]
        body = bytearray(44)
        struct.pack_into('<BBHBBBB', body, 0, 3, 0x0a, 44 + 122 * len(batch),
                         len(batch), 2, int(start + len(batch) == len(records)), 0)
        body[8:10] = country.encode('ascii')
        body[12:44] = bitmap
        out.append(bytes(body) + b''.join(batch))
    return out
