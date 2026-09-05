# SPDX-License-Identifier: MIT
"""Create a private signed catalog with downloaded payloads by default."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit, quote
import ipaddress

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from boot_inputs import load_profile
from build_payload import descriptor


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as reader:
        while block := reader.read(4 * 1024 * 1024):
            hasher.update(block)
    return 'sha256:' + hasher.hexdigest()


def build(engine, payload, destination, artifact_base_url=None, bundle_payload=False, private_http=False, execution_scratch_bytes=None):
    if artifact_base_url is None:
        raise ValueError("an explicit HTTPS artifact base URL is required; placeholder URLs are not usable releases")
    parsed = urlsplit(artifact_base_url)
    valid_scheme = parsed.scheme == 'https'
    if private_http and parsed.scheme == 'http' and parsed.hostname:
        address = ipaddress.ip_address(parsed.hostname)
        valid_scheme = any(address in ipaddress.ip_network(network) for network in
                           ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '127.0.0.0/8'))
    if (not valid_scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or artifact_base_url.endswith('/')):
        raise ValueError("artifact base must be an HTTPS directory without credentials, query or trailing slash")
    if type(execution_scratch_bytes) is not int or not 0 < execution_scratch_bytes < 2**64:
        raise ValueError("a qualified positive execution scratch budget is required")
    templates = json.loads((engine / 'installer_data.json').read_text()).get('os_list', [])
    if len(templates) != 1 or templates[0].get('package') != payload.name:
        raise ValueError("metadata package does not match payload filename")
    profile = load_profile(Path(__file__).parent / 'profiles/j713.json')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    engine_receipt = json.loads((engine / 'receipt.json').read_text())
    for role, path in (('engine', engine / ('installer-' + engine_receipt['version'] + '.tar.gz')),
                       ('metadata', engine / 'installer_data.json')):
        if descriptor(path) != engine_receipt.get(role):
            raise ValueError('engine receipt mismatch: ' + role)
    payload_receipt = json.loads(payload.with_suffix('.receipt.json').read_text())
    if descriptor(payload) != payload_receipt.get('payload'):
        raise ValueError('payload receipt mismatch')
    if (payload_receipt.get('profile') != profile
            or templates[0].get('cleanroom', {}).get('sources') != profile['sources']):
        raise ValueError('release component profile mismatch')
    destination.mkdir(exist_ok=False)
    assets = destination / 'Assets'
    assets.mkdir()
    model = {'deviceIdentifier': profile['device_identifier'], 'status': 'enabled',
             'operation': 'install', 'engineFamily': 'cleanroom',
             'executionScratchBytes': execution_scratch_bytes,
             'componentRevisions': profile['sources'],
             'downstreamRevision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
             'engineVersion': engine_receipt['version'], 'evidenceRevision': payload.stem}
    for role, source in (
        ('engine', engine / ('installer-' + engine_receipt['version'] + '.tar.gz')),
        ('metadata', engine / 'installer_data.json'), ('payload', payload),
    ):
        if source.is_symlink() or not source.is_file():
            raise ValueError('unsafe artifact: ' + role)
        # Only the inspection engine belongs in the app. The existing verified
        # downloader obtains metadata and OS payload from the signed URLs.
        model[role + 'Digest'] = digest(source)
        model[role + 'Artifact'] = {'sourceURL': artifact_base_url + '/' + quote(source.name),
                                   'fileName': source.name, 'sizeBytes': source.stat().st_size}
        if role == 'engine' or bundle_payload:
            shutil.copyfile(source, assets / source.name)
            (assets / source.name).chmod(0o444)
    catalog = {'schemaVersion': 4, 'sequence': int(time.time()),
               'issuedAt': (now - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z'),
               'expiresAt': (now + timedelta(days=30)).isoformat().replace('+00:00', 'Z'),
               'models': [model]}
    data = (json.dumps(catalog, sort_keys=True, separators=(',', ':')) + '\n').encode()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    (destination / 'catalog.json').write_bytes(data)
    (destination / 'catalog.json.sig').write_bytes(key.sign(data))
    (destination / 'trust-root.ed25519.pub').write_bytes(public)
    release_descriptor = {'schema_version': 1,
                  'catalog_url': artifact_base_url + '/catalog.json',
                  'catalog_signature_url': artifact_base_url + '/catalog.json.sig',
                  'trust_root_fingerprint': 'sha256:' + hashlib.sha256(public).hexdigest(),
                  'helper_mach_service_name': 'com.omarchy.mx.installer.helper',
                  'helper_code_signing_requirement': 'identifier "com.omarchy.mx.installer.helper" and cdhash H"' + '0' * 40 + '"'}
    (destination / 'release.json').write_text(json.dumps(release_descriptor, indent=2) + '\n')
    # The private catalog key is not retained or promoted to a public root.
    print(json.dumps({'sequence': catalog['sequence'], 'model': model['deviceIdentifier'],
                      'catalog_sha256': hashlib.sha256(data).hexdigest()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('engine', 'payload', 'destination'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--artifact-base-url', required=True,
                        help='HTTPS directory serving the exact engine, metadata, and payload files')
    parser.add_argument('--execution-scratch-bytes', type=int, required=True,
                        help='qualified peak engine workspace bytes, excluding handoff copies')
    parser.add_argument('--private-http', action='store_true',
                        help='owner-authorized local HTTP test origin only')
    parser.add_argument('--bundle-payload', action='store_true',
                        help='explicit offline packaging; not the normal download flow')
    build(**vars(parser.parse_args()))
