#!/bin/bash
# Private J713 fresh-install reset. --check only inspects the current layout.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077
mode=${1:-}
[[ $# -le 1 && ( -z $mode || $mode == "--check" ) ]] || {
  echo "Usage: $0 [--check]" >&2
  exit 64
}
work=$(mktemp -d "${TMPDIR:-/tmp}/omarchy-uninstall.XXXXXX")
trap 'rm -rf "$work"' EXIT
cat > "$work/uninstall.py" <<'PYTHON'
import json
import os
import plistlib
import subprocess
import sys

DISKUTIL = '/usr/sbin/diskutil'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def plist(*args):
    return plistlib.loads(subprocess.check_output(args))


def info(device):
    return plist(DISKUTIL, 'info', '-plist', device)


def partition(record):
    require(record.get('Internal') and record.get('PartitionMapPartition'),
            'Expected an internal physical partition.')
    return {key: record[key] for key in (
        'DiskUUID', 'Content', 'ParentWholeDisk',
        'PartitionMapPartitionOffset', 'IOKitSize')}


def inventory():
    root = info('/')
    require(root.get('Internal') and root.get('APFSVolumeGroupID'),
            'Run this from the installed internal macOS system.')
    stores = root.get('APFSPhysicalStores', [])
    require(len(stores) == 1, 'Multiple macOS physical stores are unsupported.')
    store = stores[0]['APFSPhysicalStore']
    physical_disk = info(store)['ParentWholeDisk']
    disks = plist(DISKUTIL, 'list', '-plist', physical_disk)['AllDisksAndPartitions']
    physical = next(d for d in disks if d['DeviceIdentifier'] == physical_disk)
    require(physical['Content'] == 'GUID_partition_scheme', 'Expected a GPT disk.')
    records = [info(p['DeviceIdentifier']) for p in physical['Partitions']]
    records.sort(key=lambda p: p['PartitionMapPartitionOffset'])
    containers = plist(DISKUTIL, 'apfs', 'list', '-plist')['Containers']
    return root, store, records, containers


def make_plan(root, store, records, containers):
    kinds = [p['Content'] for p in records]
    clean = ['Apple_APFS_ISC', 'Apple_APFS', 'Apple_APFS_Recovery']
    installed = clean[:2] + ['Apple_APFS', 'EFI', 'Linux Filesystem',
                             'Linux Filesystem'] + clean[2:]
    require(kinds in (clean, installed),
            'Unrecognized partition layout; nothing will be erased.')
    require(records[1]['DeviceIdentifier'] == store,
            'The macOS partition is not in the expected position.')
    parts = [partition(p) for p in records]
    require(len({p['DiskUUID'] for p in parts}) == len(parts), 'Duplicate partition UUID.')
    require(len({p['ParentWholeDisk'] for p in parts}) == 1, 'Mixed physical disks.')
    for left, right in zip(parts, parts[1:]):
        gap = right['PartitionMapPartitionOffset'] - (
            left['PartitionMapPartitionOffset'] + left['IOKitSize'])
        require(gap >= 0, 'Overlapping partition boundaries.')
        if kinds == installed:
            require(gap <= 1024 * 1024, 'Unexpected gap between installed partitions.')
    if kinds == installed:
        stub, esp, boot, linux = records[2:6]
        matching = [c for c in containers if any(
            s['DeviceIdentifier'] == stub['DeviceIdentifier'] for s in c['PhysicalStores'])]
        require(len(matching) == 1 and len(matching[0]['PhysicalStores']) == 1,
                'The Omarchy stub must be a separate, single-store container.')
        volumes = matching[0]['Volumes']
        role_names = {(tuple(v['Roles']), v['Name']) for v in volumes}
        require(len(volumes) == 4 and role_names == {
            (('System',), 'Omarchy'), (('Data',), 'Omarchy - Data'),
            (('Preboot',), 'Preboot'), (('Recovery',), 'Recovery')},
            'The neighboring APFS container is not the expected Omarchy stub.')
        require(matching[0]['ContainerReference'] != root['APFSContainerReference'],
                'Refusing to erase the running macOS container.')
        require(2 * 1024**3 <= stub['IOKitSize'] <= 4 * 1024**3,
                'Unexpected Omarchy stub size.')
        require(esp.get('VolumeName') == 'EFI - OMARC' and esp['IOKitSize'] == 524288000,
                'The EFI partition is not the Omarchy installer ESP.')
        require(boot['IOKitSize'] == 2147483648 and linux['IOKitSize'] >= 34359738368,
                'Unexpected Omarchy Linux partition sizes.')
    return {'macos_group': root['APFSVolumeGroupID'], 'partitions': parts,
            'remove': parts[2:-1], 'macos': parts[1],
            'maximum_macos_bytes': parts[-1]['PartitionMapPartitionOffset']
            - parts[1]['PartitionMapPartitionOffset']}


def default_is_macos(plan):
    boot = plist('/usr/sbin/bless', '--getBoot', '--plist')['Boot Volume']
    return info(boot).get('APFSVolumeGroupID') == plan['macos_group']


def check_remaining(plan, removed=(), grown=False):
    root, store, records, containers = inventory()
    require(root['APFSVolumeGroupID'] == plan['macos_group'], 'Booted macOS changed.')
    current = {p['DiskUUID']: p for p in records}
    expected = {p['DiskUUID']: p for p in plan['partitions'] if p['DiskUUID'] not in removed}
    require(set(current) == set(expected), 'Partition identities changed; stopping.')
    for uuid, wanted in expected.items():
        observed = partition(current[uuid])
        if grown and uuid == plan['macos']['DiskUUID']:
            target = plan['maximum_macos_bytes']
            require(target - 1024 * 1024 <= observed['IOKitSize'] <= target,
                    'macOS did not expand to the Recovery boundary.')
            observed['IOKitSize'] = wanted['IOKitSize']
        require(observed == wanted, 'Partition type or boundaries changed; stopping.')
    return current


def run(*args):
    print('Running:', ' '.join(args), flush=True)
    subprocess.run(args, check=True)


def execute(plan, user):
    check_remaining(plan)
    if not default_is_macos(plan):
        print('Selecting this macOS as the default startup disk.', flush=True)
        print('macOS may ask for your volume-owner password again.', flush=True)
        run('/usr/sbin/bless', '--mount', '/', '--setBoot', '--user', user)
    require(default_is_macos(plan),
            'Choose this macOS in System Settings > General > Startup Disk, then try again.')
    removed = []
    # Delete root, boot and ESP first; deleteContainer then removes the stub
    # partition itself on current macOS. Always resolve fresh device numbers.
    for target in reversed(plan['remove']):
        current = check_remaining(plan, removed)
        require(default_is_macos(plan), 'Default startup disk changed; stopping.')
        device = current[target['DiskUUID']]['DeviceIdentifier']
        if target['Content'] == 'Apple_APFS':
            run(DISKUTIL, 'apfs', 'deleteContainer', device)
        else:
            run(DISKUTIL, 'eraseVolume', 'free', 'free', device)
        removed.append(target['DiskUUID'])
        check_remaining(plan, removed)
    current = check_remaining(plan, removed)
    run(DISKUTIL, 'apfs', 'resizeContainer',
        current[plan['macos']['DiskUUID']]['DeviceIdentifier'], '0')
    check_remaining(plan, removed, grown=True)
    require(default_is_macos(plan), 'Default startup disk verification failed.')
    print('\nDone. Omarchy partitions are gone and macOS occupies all available space.', flush=True)
    print('Apple ISC and System Recovery are preserved. Ready for a fresh installation.', flush=True)


def main():
    check_only = sys.argv[1:] == ['--check']
    require(check_only or not sys.argv[1:], 'Unknown argument.')
    require(sys.platform == 'darwin', 'This script requires macOS.')
    require(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.model']).strip() == b'Mac16,12',
            'This private uninstaller supports the J713 MacBook Air M4 only.')
    plan = make_plan(*inventory())
    print('Omarchy partition reset\n', flush=True)
    for record, label in zip(plan['remove'], ('Omarchy APFS stub', 'Omarchy EFI', 'Linux boot', 'Linux root')):
        print('Remove: %-20s %8.2f GB  %s' % (label, record['IOKitSize'] / 1e9, record['DiskUUID']), flush=True)
    print('Expand macOS to approximately %.2f GB.' % (plan['maximum_macos_bytes'] / 1e9), flush=True)
    print('Preserve: running macOS, Apple ISC and System Recovery.', flush=True)
    if check_only:
        print('CHECK ONLY: no startup-disk, partition or filesystem changes.', flush=True)
        return
    require(os.geteuid() == 0, 'Administrator privileges required.')
    user = os.environ.get('SUDO_USER', '')
    require(user and user != 'root', 'Launch this script from your macOS user account.')
    if not plan['remove'] and plan['macos']['IOKitSize'] >= plan['maximum_macos_bytes'] - 1024 * 1024:
        print('Already reset. Nothing to do.', flush=True)
        return
    print('\nThis permanently erases all data in the listed Omarchy partitions.', flush=True)
    if input('Type ERASE to continue: ').strip() != 'ERASE':
        print('Cancelled. Nothing changed.', flush=True)
        return
    require(make_plan(*inventory()) == plan, 'Layout changed after confirmation; stopping.')
    execute(plan, user)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, KeyError, StopIteration, OSError) as error:
        print('\nSTOPPED: ' + str(error), file=sys.stderr, flush=True)
        print('No further operations will run. Keep this log if assistance is needed.', file=sys.stderr)
        sys.exit(1)
PYTHON
if [[ $mode == "--check" ]]; then
  /usr/bin/python3 "$work/uninstall.py" --check
  exit 0
fi
log_dir="$HOME/Library/Logs/Omarchy-Uninstall"
mkdir -p "$log_dir"
log="$log_dir/uninstall-$(date +%Y%m%d-%H%M%S)-$$.log"
echo "Log: $log"
set +e
sudo /usr/bin/python3 -u "$work/uninstall.py" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
set -e
echo
read -r -p "Press Return to close this window. " reply || true
exit "$status"
