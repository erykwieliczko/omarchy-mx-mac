"""Prepare metadata-only recipes; fetch selected Apple segments without an image.

This is a read-only experiment, not an installer input or production trust root.
The caller must independently pin --recipe-sha256 for the fetch operation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import requests

from aea import Archive, digest, prefix_from_file, require


def normalize(data, transform):
    if transform == 'identity':
        return data
    require(transform == 'nvram', 'Unknown output transform')
    lines = []
    for line in data.decode('ascii').split('\n'):
        if line:
            key, value = line.split('=', 1)
            lines.append(key.strip() + '=' + value + '\n')
    return ''.join(lines).encode('ascii')


def safe_name(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts
            and str(path) == name and name not in ('', '.'), 'Invalid output name')
    return name


def range_get(url, total, offset, size):
    require(type(offset) is int and type(size) is int and 0 <= offset
            and 0 < size <= 4 * 1024 * 1024 and offset + size <= total, 'Invalid HTTP extent')
    def admitted(value):
        u = urlsplit(value)
        return (u.scheme == 'https' and u.hostname == 'updates.cdn-apple.com'
                and u.port in (None, 443) and not u.username and not u.password
                and not u.query and not u.fragment)
    require(admitted(url), 'Not the admitted Apple CDN')
    headers = {'Range': f'bytes={offset}-{offset + size - 1}', 'Accept-Encoding': 'identity'}
    # No automatic redirect: never send a request outside the admitted origin.
    with requests.get(url, headers=headers, stream=True, allow_redirects=False, timeout=(15, 60)) as response:
        require(response.status_code == 206, 'Apple did not return a byte range')
        require(response.headers.get('Content-Range') == f'bytes {offset}-{offset + size - 1}/{total}',
                'Wrong Content-Range / source length')
        require(response.headers.get('Content-Encoding', 'identity') == 'identity', 'Unexpected encoding')
        require(int(response.headers.get('Content-Length', '-1')) == size, 'Wrong Content-Length')
        data = response.raw.read(size + 1)
        require(len(data) == size, 'Short or oversized HTTP range')
        return data


def merge_spans(spans):
    merged = []
    for start, end in sorted((r['offset'], r['offset'] + r['size']) for r in spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def locate_storage(data, regions, segment_sizes, chosen):
    """Map bytes to traced source reads, including fragmented resource forks.

    Choose long exact matches, preferring already-needed AEA segments. The
    output contains offsets, lengths and hashes only, never a byte dictionary.
    This is a bounded greedy optimizer, not a claim of a global minimum.
    """
    result, position = [], 0
    while position < len(data):
        seed = data[position:position + 32]
        candidates = []
        for base, block in regions:
            offset, attempts = block.find(seed), 0
            while offset >= 0 and attempts < 64:
                maximum = min(len(data) - position, len(block) - offset)
                # Longest common prefix; logarithmic comparisons avoid a Python
                # byte loop over multi-megabyte extents.
                low, high = len(seed), maximum
                while low < high:
                    mid = (low + high + 1) // 2
                    if data[position:position + mid] == block[offset:offset + mid]:
                        low = mid
                    else:
                        high = mid - 1
                start = base + offset
                ids = set(range(start // 1048576, (start + low - 1) // 1048576 + 1))
                cost = sum(segment_sizes[i] for i in ids - chosen)
                candidates.append((cost / low, -low, start, ids))
                offset = block.find(seed, offset + 1)
                attempts += 1
        require(candidates, 'Storage bytes were not found in traced physical reads')
        _, negative_length, start, ids = min(candidates, key=lambda x: x[:3])
        length = -negative_length
        result.append({'offset': start, 'size': length})
        chosen.update(ids)
        position += length
    return result


def verify_local(path, record):
    with Path(path).open('rb') as file:
        require(os.fstat(file.fileno()).st_size == record['size_bytes'], 'Local baseline size mismatch')
        require(hashlib.file_digest(file, 'sha256').hexdigest() == record['sha256'],
                'Local baseline SHA-256 mismatch')


def prime(args):
    lock = json.loads(args.lock.read_text())
    require(not args.recipe.exists(), 'Recipe output already exists')
    verify_local(args.aea, lock['system_image'])
    verify_local(args.image, lock['system_image']['decoded'])
    args.workspace.mkdir()
    trace = subprocess.check_output([str(args.apfs_helper), str(args.image),
                                     str(args.selection), str(args.workspace / 'baseline')])
    (args.workspace / 'trace.json').write_bytes(trace)
    files = json.loads(trace)
    with args.aea.open('rb') as encrypted, args.image.open('rb') as raw:
        read = lambda offset, size: os.pread(encrypted.fileno(), size, offset)
        prefix = prefix_from_file(read)
        archive = Archive(read, prefix, args.key_helper)
        archive.index()
        chosen, outputs = set(), []
        segment_sizes = [archive.segments[i]['size'] for i in range(len(archive.segments))]
        for file in files:
            selection = file['selection']
            name = safe_name(selection['name'])
            original = (args.workspace / 'baseline' / name).read_bytes()
            final = normalize(original, selection['transform'])
            require(not selection.get('expected_sha256') or digest(final) == selection['expected_sha256'],
                    'Selected file does not match the existing firmware lock')
            regions = [(start, os.pread(raw.fileno(), end - start, start))
                       for start, end in merge_spans(file['reads'])]
            storage = []
            for storage_name in file['storage']:
                data = (args.workspace / 'baseline' / safe_name(storage_name)).read_bytes()
                storage.append({'size': len(data), 'sha256': digest(data),
                                'spans': locate_storage(data, regions, segment_sizes, chosen)})
            outputs.append({'source': selection['source'], 'name': name,
                            'transform': selection['transform'], 'compressed': file['compressed'],
                            'raw_sha256': digest(original), 'sha256': digest(final),
                            'size': len(final), 'storage': storage})
        selected = {str(i): {'offset': archive.segments[i]['offset'],
                            'size': archive.segments[i]['size'],
                            'sha256': digest(read(archive.segments[i]['offset'], archive.segments[i]['size']))}
                    for i in sorted(chosen)}
        zip_record = lock['members'][lock['system_image']['member']]
        require(zip_record['compression'] == 0, 'Outer ZIP member must be stored, not deflated')
        url, total = lock['ipsw']['url'], lock['ipsw']['size_bytes']
        header_offset = zip_record['header_offset']
        header = range_get(url, total, header_offset, 30)
        require(header[:4] == b'PK\x03\x04' and struct.unpack_from('<H', header, 8)[0] == 0,
                'Unsupported ZIP local header')
        namesize, extrasize = struct.unpack_from('<HH', header, 26)
        tail = range_get(url, total, header_offset + 30, namesize + extrasize)
        require(tail[:namesize].decode('utf8') == lock['system_image']['member'], 'Wrong ZIP member')
        zip_header = header + tail
        recipe = {'schema': 1, 'source': lock['ipsw'], 'system_image': lock['system_image'],
                  'member_offset': header_offset + len(zip_header),
                  'zip_header': {'offset': header_offset, 'size': len(zip_header), 'sha256': digest(zip_header)},
                  'prefix': {'offset': 0, 'size': len(prefix), 'sha256': digest(prefix)},
                  'headers': [c for c in archive.clusters if c['cluster'] in {i // archive.width for i in chosen}],
                  'segments': selected, 'files': outputs}
    args.recipe.write_text(json.dumps(recipe, indent=2) + '\n')
    network = len(zip_header) + len(prefix) + sum(c['size'] for c in recipe['headers'])
    network += sum(s['size'] for s in selected.values())
    print(json.dumps({'recipe_sha256': digest(args.recipe.read_bytes()),
                      'recipe_bytes': args.recipe.stat().st_size, 'selected_segments': len(selected),
                      'apple_range_bytes': network, 'output_bytes': sum(f['size'] for f in outputs)}, indent=2))


def fetch(args):
    encoded = args.recipe.read_bytes()
    require(digest(encoded) == args.recipe_sha256, 'Recipe SHA-256 mismatch')
    recipe = json.loads(encoded)
    require(recipe['schema'] == 1, 'Unsupported recipe version')
    if args.file:
        requested = set(args.file)
        require(requested <= {f['name'] for f in recipe['files']}, 'File missing from recipe')
        recipe['files'] = [f for f in recipe['files'] if f['name'] in requested]
        needed = {i for f in recipe['files'] for s in f['storage'] for p in s['spans']
                  for i in range(p['offset'] // 1048576, (p['offset'] + p['size'] - 1) // 1048576 + 1)}
        recipe['segments'] = {i: r for i, r in recipe['segments'].items() if int(i) in needed}
        recipe['headers'] = [r for r in recipe['headers'] if r['cluster'] in {i // 256 for i in needed}]
    require(not args.output.exists(), 'Output already exists')
    source = recipe['source']
    records = [recipe['prefix'], *recipe['headers'], *recipe['segments'].values()]
    started = time.monotonic()
    def get(record, base=recipe['member_offset']):
        data = range_get(source['url'], source['size_bytes'], base + record['offset'], record['size'])
        require(digest(data) == record['sha256'], 'Encrypted range SHA-256 mismatch')
        return data
    get(recipe['zip_header'], 0)
    # The complete dependency set is fetched into bounded memory; no full image,
    # development cache, mount, or locally extracted file is accessible here.
    require(sum(r['size'] for r in records) <= 256 * 1024 * 1024, 'Prototype range budget exceeded')
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        data = list(pool.map(get, records))
    cache = {(r['offset'], r['size']): value for r, value in zip(records, data)}
    def read(offset, size):
        require((offset, size) in cache, 'Read outside the precomputed dependency set')
        return cache[offset, size]
    archive = Archive(read, data[0], args.key_helper)
    require(archive.size == recipe['system_image']['decoded']['size_bytes']
            and archive.encrypted_size == recipe['system_image']['size_bytes'], 'Wrong AEA image identity')
    archive.index_selected(recipe['headers'])
    decoded = {int(i): archive.decode_segment(int(i), r['sha256']) for i, r in recipe['segments'].items()}
    network_seconds = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='range-extract-', dir=args.output.parent) as pending:
        pending = Path(pending)
        results = []
        for index, file in enumerate(recipe['files']):
            name = safe_name(file['name'])
            storage_paths = []
            for part, storage in enumerate(file['storage']):
                result = bytearray()
                for span in storage['spans']:
                    offset, remaining = span['offset'], span['size']
                    while remaining:
                        segment, within = divmod(offset, archive.segment_size)
                        require(segment in decoded, 'Missing required segment')
                        count = min(remaining, len(decoded[segment]) - within)
                        require(count > 0, 'Invalid plaintext span')
                        result.extend(decoded[segment][within:within + count])
                        offset += count
                        remaining -= count
                require(len(result) == storage['size'] and digest(result) == storage['sha256'],
                        'APFS storage SHA-256 mismatch')
                path = pending / f'storage-{index}-{part}'
                path.write_bytes(result)
                storage_paths.append(path)
            if file['compressed']:
                raw = pending / f'raw-{index}'
                subprocess.run([str(args.apfs_helper), 'decode', str(storage_paths[0]),
                                str(storage_paths[1]) if len(storage_paths) > 1 else '-', str(raw)], check=True)
                original = raw.read_bytes()
            else:
                original = storage_paths[0].read_bytes()
            require(digest(original) == file['raw_sha256'], 'Extracted source SHA-256 mismatch')
            final = normalize(original, file['transform'])
            require(len(final) == file['size'] and digest(final) == file['sha256'], 'Final file SHA-256 mismatch')
            path = pending / 'files' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(final)
            results.append({'name': name, 'size': len(final), 'sha256': digest(final)})
        # Nothing becomes an accepted output until every file has passed.
        (pending / 'files').rename(args.output)
    receipt = {'status': 'passed', 'recipe_sha256': args.recipe_sha256,
               'network_bytes': sum(r['size'] for r in records) + recipe['zip_header']['size'],
               'range_requests': len(records) + 1, 'selected_segments': len(decoded),
               'network_and_aea_seconds': round(network_seconds, 3),
               'elapsed_seconds': round(time.monotonic() - started, 3),
               'full_image_used': False, 'outputs': results}
    # FCS key/envelope traffic is small and separate from range-byte accounting.
    print(json.dumps(receipt, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prime')
    for field in ('aea', 'image', 'selection', 'lock', 'workspace'):
        p.add_argument('--' + field, type=Path, required=True)
    f = commands.add_parser('fetch')
    f.add_argument('--recipe-sha256', required=True)
    f.add_argument('--output', type=Path, required=True)
    f.add_argument('--workers', type=int, default=6, choices=range(1, 9))
    f.add_argument('--file', action='append', help='Extract only this output name; repeat for more files')
    for command in (p, f):
        for field in ('recipe', 'key-helper', 'apfs-helper'):
            command.add_argument('--' + field, type=Path, required=True)
    args = parser.parse_args()
    (prime if args.command == 'prime' else fetch)(args)


if __name__ == '__main__':
    main()
