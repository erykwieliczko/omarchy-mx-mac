# Source: linux-enablement-mac-alpha 6b4eb9858dc5841e3a848cd21b727ab32d7f44e6
#!/usr/bin/env python3
"""Parse original policy data and serialize documented policy subsets.

Offline only. These subsets are NOT a complete RF startup/power transaction.
Normal2G records and common-path now follow recovered host-format3 contracts.
Full header bitmap/unsupported-rate publication remain separate prerequisites.
Original integer units are preserved; sentinel bytes are not numerical limits.
"""
import csv
from functools import lru_cache
import re
import struct


# Tables are read-only to all consumers. Keep the four original table files
# per worker instead of reparsing every country in every conversion step.
@lru_cache(maxsize=4)
def parse_tables(data):
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('policy input too large')
    text = data.decode('ascii').replace('\\r\\n', '\n').replace('\\n', '\n')
    countries = {}
    country, table = '', None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(('{Ver:', '<Ver:', '#')):
            continue
        if re.fullmatch(r'\[[A-Z0-9]{2}\]', line):
            if table is not None:
                raise ValueError('unclosed policy table')
            country = line[1:-1]
            if country in countries:
                raise ValueError('duplicate country')
            countries[country] = {}
        elif line.startswith('</') and line.endswith('>'):
            if table is None or line[2:-1].strip() != table['name']:
                raise ValueError('unmatched table close')
            table = None
        elif line.startswith('<') and line.endswith('>'):
            if table is not None:
                raise ValueError('nested table')
            fields = [s.strip() for s in next(csv.reader([line[1:-1]]))]
            target = countries.setdefault(country, {})
            if not fields[0] or fields[0] in target or len(fields) < 2:
                raise ValueError('duplicate/invalid table')
            table = dict(name=fields[0], columns=fields[1:], records={})
            target[fields[0]] = table
        else:
            if table is None:
                raise ValueError('row outside table')
            fields = [s.strip() for s in next(csv.reader([line]))]
            if len(fields) != len(table['columns']) + 1 or fields[0] in table['records']:
                raise ValueError('duplicate row or column count mismatch')
            values = []
            for token in fields[1:]:
                if token in ('X', 'x', 'Y'):
                    values.append(token)
                elif re.fullmatch(r'-?[0-9]+', token):
                    value = int(token)
                    if not -128 <= value <= 127:
                        raise ValueError('encoded limit outside signed-byte range')
                    values.append(value)
                else:
                    raise ValueError('unknown encoded limit')
            table['records'][fields[0]] = values
    if table is not None:
        raise ValueError('unterminated table')
    return countries


def header(table_type, country, count, body_size):
    if country and not re.fullmatch('[A-Z0-9]{2}', country):
        raise ValueError('country must be an explicit two-character code')
    if not 0 < count < 256 or not 0 <= body_size <= 1020 - 44:
        raise ValueError('unsupported table extent')
    out = bytearray(44)
    out[1] = 2
    struct.pack_into('<H', out, 2, 44 + body_size)
    out[4:8] = bytes((count, 1, 1, table_type))
    out[8:10] = country.encode('ascii') if country else b'\0\0'
    return out


def subbands(table, count, width, allow_x=False):
    expected = [f'subband{i}' for i in range(1, count + 1)]
    if set(table['records']) != set(expected) or len(table['columns']) != width:
        raise ValueError('incomplete or unexpected subband geometry')
    out = bytearray()
    for index, name in enumerate(expected):
        values = table['records'][name]
        if len(values) != width:
            raise ValueError('wrong subband width')
        out.append(index)
        for value in values:
            if value == 'X' and allow_x:
                value = 127  # Qualified specifically by the captured SDB format.
            if type(value) is not int or not -128 <= value <= 127:
                raise ValueError('unqualified sentinel or encoded value')
            out.append(value & 255)
    return out


def sar(data, country):
    table = parse_tables(data)[country]['tab']
    if table['columns'] != ['siso_wf0', 'siso_wf1', 'mimo_wf0', 'mimo_wf1']:
        raise ValueError('SAR column order mismatch')
    body = subbands(table, 20, 4)
    return bytes(header(1, country, 20, len(body)) + body)


def sdb(data):
    table = parse_tables(data)['']['sdb']
    columns = [f'{rate}_{mode}_wf{path}' for mode in ('siso', 'mimo')
               for rate in ('cck', 'ofdm', 'ofdma') for path in (0, 1)]
    if table['columns'] != columns:
        raise ValueError('SDB column order mismatch')
    body = subbands(table, 17, 12, allow_x=True)
    return bytes(header(2, '', 17, len(body)) + body)


def wcal(data):
    if not 0 < len(data) <= 1024:
        raise ValueError('WCAL length must be 1..1024')
    return struct.pack('<BBH', 3, 0, len(data)) + data


def common_path(data, country):
    table = parse_tables(data)[country]['common_path_backoff']
    names = [f'ch{i:03d}' for i in range(1, 14)]
    if table['columns'] != ['backoff'] or set(table['records']) != set(names):
        raise ValueError('common-path requires exactly channels1..13 and one backoff column')
    values = [table['records'][name][0] for name in names]
    if any(type(v) is not int or not 0 <= v <= 127 for v in values):
        raise ValueError('invalid common-path byte')
    return bytes(header(4, '', 13, 14) + bytes(values) + b'\0')


RATE_CATEGORIES = ('cck', 'ofdm', 'ht20', 'ht40', 'vht20', 'vht40', 'vht80',
                   'vht160', 'ru26', 'ru52', 'ru106', 'ru242', 'ru484', 'ru996', 'ru996X2')
RATE_WIDTHS = (4, 8, 24, 24, 30, 30, 30, 30, 36, 36, 36, 36, 36, 36, 36)


def encoded_rate(value):
    if value in ('X', 'x'):
        return 0xc4  # Host unsupported-rate marker, NOT minus60 power.
    if value == 'Y':
        return 0x7f  # Host unpopulated marker, NOT a default usable limit.
    if type(value) is not int or not -128 <= value <= 127:
        raise ValueError('invalid encoded rate')
    return value & 255


def normal_2g_records(data, country, channels):
    """Format3 records only; callers still need full-country header/bitmap state."""
    if b'<Ver:03>' not in data or not re.fullmatch('[A-Z]{2}', country):
        raise ValueError('unqualified rate format/country profile')
    tables = parse_tables(data)[country]
    channels = list(channels)
    maximum = 14 if country == 'XZ' else 13  # XZ14 is an offline capture regression.
    if not channels or len(channels) != len(set(channels)) or any(
            type(ch) is not int or not 1 <= ch <= maximum for ch in channels):
        raise ValueError('invalid/duplicate channel')
    out = []
    required = {'cck', 'ofdm', 'vht20', 'ru26', 'ru52', 'ru106', 'ru242'}
    for channel in channels:
        name = f'ch{channel:03d}'
        groups = {category: [0xc4] * width for category, width in zip(RATE_CATEGORIES, RATE_WIDTHS)}
        for category, width in zip(RATE_CATEGORIES, RATE_WIDTHS):
            if category in ('ht20', 'ht40'):
                continue  # Literal format3 behavior: HT text rows are not copied.
            table = tables.get(category)
            if table is None or name not in table['records']:
                if table is None and category in required:
                    raise ValueError(f'missing required {category} table')
                # The original contextual loader initializes absent rows to
                # its unsupported marker. Several real countries omit12/13;
                # absence is not permission to use a different country's row.
                continue
            values = table['records'][name]
            if len(values) != width or len(table['columns']) != width:
                raise ValueError('rate category width mismatch')
            groups[category] = [encoded_rate(v) for v in values]
            if category == 'ofdm':
                groups['ht20'][:8] = groups['ofdm']
        packed = bytearray((channel,))
        for category, width in zip(RATE_CATEGORIES, RATE_WIDTHS):
            values = groups[category]
            if category == 'cck':
                indices = (0,)
            elif category == 'ofdm':
                indices = (0, 4, 6)
            else:
                within = (1, 5, 7) if category.startswith('ht') else (0, 3, 5)
                indices = tuple(group * (width // 3) + at for group in range(3) for at in within)
            packed.extend(values[at] for at in indices)
        assert len(packed) == 122
        out.append(bytes(packed))
    return out
