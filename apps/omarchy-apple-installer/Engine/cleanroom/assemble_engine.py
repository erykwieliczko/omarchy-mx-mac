# SPDX-License-Identifier: MIT
"""Assemble locked cleanroom sources with the admitted macOS Python runtime."""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile

from boot_inputs import load_profile
from apple_inputs import load_apple_inputs
from boot_builds import load_boot_builds
from stage_sources import stage_sources

RUNTIME_SHA256 = '063fd0765fb2057384d9653f7bf547b0471af31fc764e039d578d4fef6dce4d5'


def descriptor(path):
    return {'size_bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def assemble(checkout, inputs, destination, version, payload_name, development_apple_cache=False):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)*', version):
        raise ValueError('invalid engine version')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.zip', payload_name):
        raise ValueError('payload must be a ZIP basename')
    native = json.loads((Path(__file__).parent / 'native-artifacts.json').read_text())
    for name, expected in native['artifacts'].items():
        if descriptor(inputs / name) != expected:
            raise ValueError('native engine input differs from admission: ' + name)
    destination.mkdir(exist_ok=False)
    base = json.loads((Path(__file__).parent.parent / 'installer_data.json').read_text())['os_list'][0]
    metadata = {'os_list': []}
    for model in ('j713', 'j700'):
        profile = load_profile(Path(__file__).parent / f'profiles/{model}.json')
        template = copy.deepcopy(base)
        template['package'] = payload_name
        template['supported_fw'] = [profile['firmware']['version']]
        template['partitions'][0]['source'] = 'esp-' + model
        apple = load_apple_inputs(Path(__file__).parent / f'profiles/{model}-apple-inputs.json', profile)
        boot_catalog, _ = load_boot_builds(Path(__file__).parent / 'profiles', apple, profile)
        template['cleanroom'] = {
            'schema_version': 2, 'device_identifier': profile['device_identifier'],
            'firmware_build': profile['firmware']['build'], 'sources': profile['sources'],
            'apple_inputs': apple, 'apple_boot_builds': boot_catalog,
            'stage1': descriptor(inputs / 'm1n1-stage1-base.bin'), 'linux_firmware': apple['linux_firmware'],
        }
        metadata['os_list'].append(template)
    metadata_path = destination / 'installer_data.json'
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
    runtime = inputs / 'installer-runtime-source.tar.gz'
    if descriptor(runtime)['sha256'] != RUNTIME_SHA256:
        raise ValueError('Python runtime carrier digest mismatch')
    with tempfile.TemporaryDirectory(prefix='.engine-assembly-', dir=destination) as temporary:
        temporary = Path(temporary)
        tree = stage_sources(checkout, temporary / 'source')
        package = temporary / 'package'
        shutil.copytree(tree / 'src', package, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(tree / 'asahi_firmware', package / 'asahi_firmware', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
        with tarfile.open(runtime) as archive:
            members = [m for m in archive.getmembers() if m.name.removeprefix('./').startswith('Frameworks/')]
            archive.extractall(package, members=members, filter='data')
        if development_apple_cache:
            (package / 'cleanroom/development-apple-cache').write_text('Private development cache enabled explicitly at build time.\n')
        (package / 'boot').mkdir()
        shutil.copyfile(inputs / 'm1n1-stage1-base.bin', package / 'boot/m1n1.bin')
        (package / 'tools').mkdir(exist_ok=True)
        shutil.copyfile(inputs / 'omarchy-restore-image', package / 'tools/omarchy-restore-image')
        (package / 'tools/omarchy-restore-image').chmod(0o755)
        shutil.copyfile(inputs / 'base-images/omarchy-volume.icns', package / 'logo.icns')
        shutil.copyfile(metadata_path, package / 'installer_data.json')
        shutil.copyfile(tree / 'cleanroom-source-lock.json', package / 'cleanroom-source-lock.json')
        (package / 'version.tag').write_text(version + '\n')
        (package / 'runtime-provenance.json').write_text(json.dumps({'carrier_sha256': RUNTIME_SHA256}) + '\n')
        artifact = destination / ('installer-' + version + '.tar.gz')
        with artifact.open('xb') as writer, gzip.GzipFile(filename='', fileobj=writer, mode='wb', mtime=0, compresslevel=9) as compressor, tarfile.open(fileobj=compressor, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(package.rglob('*')):
                if '__pycache__' in path.parts:
                    continue
                name = './' + path.relative_to(package).as_posix()
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 1788566400
                info.pax_headers = {}
                info.mode &= ~0o022
                if info.isfile():
                    with path.open('rb') as reader:
                        archive.addfile(info, reader)
                else:
                    archive.addfile(info)
        receipt = {'engine': descriptor(artifact), 'metadata': descriptor(metadata_path),
                   'runtime_carrier_sha256': RUNTIME_SHA256, 'version': version,
                   'development_apple_cache': development_apple_cache}
        (destination / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('checkout', 'inputs', 'destination'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--development-apple-cache', action='store_true',
                        help='private development only: reuse verified Apple members on the build/test Mac')
    parser.add_argument('--version', required=True)
    parser.add_argument('--payload-name', required=True)
    print(json.dumps(assemble(**vars(parser.parse_args())), indent=2))
