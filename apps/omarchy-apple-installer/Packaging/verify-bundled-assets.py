#!/usr/bin/python3
"""Check every declared bundled asset before app resource sealing.

The Swift trust core separately verifies the Ed25519 signature and model policy.
"""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("release", type=Path)
parser.add_argument("--engine-only", action="store_true")
args = parser.parse_args()
release = args.release
assets = release / 'Assets'
if assets.is_symlink() or not assets.is_dir():
    raise SystemExit('missing or unsafe Assets directory')
catalog = json.loads((release / 'catalog.json').read_text())
expected = {}
for model in catalog['models']:
    for role in ('engine', 'metadata', 'payload', 'repairManifest'):
        artifact = model.get(role + 'Artifact')
        if artifact is None:
            continue
        if args.engine_only and role != 'engine':
            continue
        records = [(artifact, model[role + 'Digest'])]
        records.extend((part, part['digest']) for part in artifact.get('parts', []))
        for record, digest in records:
            name = record['fileName']
            if name in ('', '.', '..') or '/' in name or '\\' in name or urlparse(record['sourceURL']).path.rsplit('/', 1)[-1] != name:
                raise SystemExit('bundled URL basename must match the safe catalog filename')
            value = (record['sizeBytes'], digest)
            if name in expected and expected[name] != value:
                raise SystemExit('conflicting catalog artifact')
            expected[name] = value
if not expected or {p.name for p in assets.iterdir()} != set(expected):
    raise SystemExit('bundled asset inventory differs from catalog')
for name, (size, digest) in expected.items():
    path = assets / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise SystemExit('unsafe or incorrectly sized asset: ' + name)
    hasher = hashlib.sha256()
    with path.open('rb') as reader:
        while block := reader.read(4 * 1024 * 1024):
            hasher.update(block)
    if 'sha256:' + hasher.hexdigest() != digest:
        raise SystemExit('asset digest mismatch: ' + name)
print('Included asset inventory, sizes and digests verified.')
