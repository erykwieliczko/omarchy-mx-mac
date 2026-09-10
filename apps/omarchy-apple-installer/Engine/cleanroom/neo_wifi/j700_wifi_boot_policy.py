# Source: linux-enablement-mac-alpha eb18361654bef156f170f1a70530227757056933
#!/usr/bin/env python3
"""Generate country policy bodies from unchanged originals, not oracle arrays.

Files are private local build artifacts, never credentials or firmware code.
The consumer must still perform runtime PHY reconciliation and all startup gates.
"""
import argparse
from pathlib import Path
import struct
import tarfile
import hashlib
import json
import re
import shutil
from concurrent.futures import ProcessPoolExecutor
from .j700_wifi_host_model import expand_original, normal_2g_bodies, normal_5g_bodies, unsupported_rate_body
from .j700_wifi_policy import sar, common_path, sdb, parse_tables
from .j700_wifi_startup import original_config, config_records, country_sar_mode


def generate(archive, output, database, country='PL'):
    qualified = {
        'IZUBA_TxPwrLimit_MT79x1.dat': '03ee51df5231d131c1976f1d2ea7f81a1396988e63a132a318a9893e8ef5ec38',
        'IZUBA_TxPwrLimit_SAR.dat': 'd8dc9dfff2ad2d1ddf301d892e60c93f476603992f71de9e742194a395ca850d',
        'IZUBA_TxPwrLimit_2gCommonPath.dat': '43185e334cb074b9c89b71aa6440d5d22cc45e9d8dc1fdc66c8a4933f2376b6c',
        'IZUBA_TxPwrLimit_SDB.dat': '042906f4b23f05d82f211549b22f195a57d3437b137009fda0a0e40935abbfd0',
        'IZUBA_wifi.cfg': '9f1adcb329c47a25421cad3ede473e1fdf41edb36f295f17f021d2689430899d',
    }
    with tarfile.open(archive, 'r:gz') as tar:
        def read(name):
            member = tar.getmember(name)
            if not member.isfile() or member.size > 32 * 1024 * 1024:
                raise ValueError('invalid original input member')
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != qualified[name]:
                raise ValueError('unqualified original input hash: ' + name)
            return data
        rates = read('IZUBA_TxPwrLimit_MT79x1.dat')
        expanded = expand_original(rates, country)
        normal = normal_2g_bodies(rates, country, expanded)
        result = {'power-unsupported.bin': unsupported_rate_body(expanded),
                  'power-normal-0.bin': normal[0], 'power-normal-1.bin': normal[1],
                  'power-sar.bin': sar(read('IZUBA_TxPwrLimit_SAR.dat'), country),
                  'power-common.bin': common_path(read('IZUBA_TxPwrLimit_2gCommonPath.dat'), country),
                  'power-sdb.bin': sdb(read('IZUBA_TxPwrLimit_SDB.dat'))}
        result.update({f'power-normal-5g-{i}.bin': body for i, body in
                       enumerate(normal_5g_bodies(country, expanded))})
        config = original_config(read('IZUBA_wifi.cfg'))
        records = b''.join(config_records([entry])[12:80] for entry in config)
        result['config-original.bin'] = b'J7CF' + struct.pack('<I', len(config)) + records
        result['sar-mode.bin'] = b''.join(country_sar_mode(database.read_bytes(), country))
    order = ['power-unsupported.bin', 'power-normal-0.bin', 'power-normal-1.bin']
    order += [f'power-normal-5g-{i}.bin' for i in range(7)]
    order += ['power-sar.bin', 'power-common.bin', 'power-sdb.bin']
    payload = result['sar-mode.bin'] + b''.join(result[name] for name in order)
    package = struct.pack('<4sH2sBB2xI', b'J7RP', 1, country.encode('ascii'),
                          len(result['sar-mode.bin']) // 328, 13, 16 + len(payload)) + payload
    result['policy.bin'] = package
    output.mkdir(parents=True, exist_ok=True)
    for name, data in result.items():
        path = output / name
        path.write_bytes(data)
        path.chmod(0o600)
    (output / 'manifest.json').write_text(json.dumps({
        'format': 'J7RP-1', 'producer': 'j700-original-contextual-v1', 'country': country,
        'profile': 'J700 MT7932 native fullmac non-6GHz 20MHz',
        'inputs': qualified,
        'database_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
        'bodies': {name: {'length': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                   for name, data in result.items()},
    }, indent=2) + '\n')
    return result


def generate_country_job(args):
    archive, output, database, country = args
    try:
        result = generate(archive, output / country, database, country)
        return country, len(result['policy.bin']), None
    except (ValueError, KeyError) as error:
        return country, 0, str(error)


def generate_all(archive, output, database, jobs=4):
    """Build every two-letter source profile; availability is not RF permission."""
    with tarfile.open(archive, 'r:gz') as tar:
        source = tar.extractfile('IZUBA_TxPwrLimit_MT79x1.dat').read()
    countries = sorted(cc for cc in parse_tables(source) if re.fullmatch('[A-Z]{2}', cc))
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(generate_country_job,
                               [(archive, output, database, cc) for cc in countries]))
    output.mkdir(parents=True, exist_ok=True)
    index = {cc: {'length': length, 'error': error} for cc, length, error in results}
    (output / 'index.json').write_text(json.dumps(index, indent=2) + '\n')
    return index


def firmware_tree(output, destination):
    """Package original-derived profiles for initramfs AND installed /lib/firmware."""
    index = json.loads((output / 'index.json').read_text())
    target = destination / 'mediatek/mt7932'
    (target / 'policy').mkdir(parents=True, exist_ok=True)
    count = 0
    for cc, entry in index.items():
        # XZ is a vendor fallback fixture with a14-channel band, not a
        # userspace regulatory country and not this bounded driver profile.
        if entry['error'] or cc == 'XZ':
            continue
        source = output / cc / 'policy.bin'
        manifest = json.loads((output / cc / 'manifest.json').read_text())
        data = source.read_bytes()
        if manifest['country'] != cc or hashlib.sha256(data).hexdigest() != manifest['bodies']['policy.bin']['sha256']:
            raise ValueError('package/manifest mismatch: ' + cc)
        path = target / 'policy' / (cc + '.bin')
        shutil.copyfile(source, path)
        path.chmod(0o644)
        count += 1
    shutil.copyfile(output / 'PL/config-original.bin', target / 'config-original.bin')
    (target / 'config-original.bin').chmod(0o644)
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--country', default='PL')
    parser.add_argument('--all', action='store_true', help='generate all two-letter original profiles, no fallback')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--firmware-dir', type=Path, help='also populate this firmware staging tree (--all)')
    args = parser.parse_args()
    if args.all:
        index = generate_all(args.archive, args.output, args.database, args.jobs)
        failed = {cc: entry['error'] for cc, entry in index.items() if entry['error']}
        print('Generated profiles:', len(index) - len(failed), 'rejected:', failed)
        if args.firmware_dir:
            print('Packaged country profiles:', firmware_tree(args.output, args.firmware_dir))
    else:
        generated = generate(args.archive, args.output, args.database, args.country)
        print('Generated', args.country, 'original-input policy:', ', '.join(f'{k}={len(v)}' for k, v in generated.items()))
