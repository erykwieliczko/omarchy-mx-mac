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
import getpass
import os
import plistlib
import signal
import time
import subprocess
import sys

DISKUTIL = '/usr/sbin/diskutil'
HELPER_SERVICE = 'system/com.omarchy.mx.installer.helper'
HELPER_PLIST = '/Library/LaunchDaemons/com.omarchy.mx.installer.helper.plist'
APP = '/Applications/Omarchy MX Mac Installer.app/Contents/'



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


def run(*args, password=None):
    print('Running:', ' '.join(args), flush=True)
    result = subprocess.run(args, input=password, stdin=subprocess.DEVNULL if password is None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout
    if password:
        output = output.replace(password.rstrip(b'\n'), b'[redacted]')
    print(output.decode(errors='replace'), end='', flush=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, output=output)


def processes():
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,lstart=,comm='], text=True)
    result = {}
    for line in output.splitlines():
        fields = line.split(None, 7)
        if len(fields) == 8:
            result[int(fields[0])] = (int(fields[1]), ' '.join(fields[2:7]), fields[7])
    return result


def installer_process(command):
    command = command.removeprefix('/private')
    return command in (APP + 'MacOS/OmarchyAppleInstallerApp',
                       APP + 'Resources/omarchy-apple-installer-helper') or (
        command.startswith('/var/db/com.omarchy.mx.installer/engine-execution-')
        and '/bundle/' in command)


def installer_tree(snapshot):
    selected = {pid for pid, record in snapshot.items() if installer_process(record[2])}
    while True:
        expanded = selected | {pid for pid, record in snapshot.items() if record[0] in selected}
        if expanded == selected:
            return {pid: snapshot[pid] for pid in selected}
        selected = expanded


def signal_same_process(pid, record, sig):
    current = processes().get(pid)
    # Parent can change when launchd removes the helper; start time and executable cannot.
    if current is not None and current[1:] == record[1:]:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def stop_installer():
    print('Force-stopping the Omarchy installer and all its subprocesses.', flush=True)
    tracked = installer_tree(processes())
    bootout = None
    try:
        # Freeze the tree before killing parents so it cannot spawn more disk writers.
        for _ in range(8):
            for pid, record in tracked.items():
                signal_same_process(pid, record, signal.SIGSTOP)
            latest = installer_tree(processes())
            if all(pid in tracked and tracked[pid][1:] == record[1:]
                   for pid, record in latest.items()):
                break
            tracked.update(latest)
        else:
            raise RuntimeError('Installer processes kept restarting; stopping.')
        registered = subprocess.run(['/bin/launchctl', 'print', HELPER_SERVICE],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if registered:
            bootout = subprocess.Popen(['/bin/launchctl', 'bootout', HELPER_SERVICE],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
    finally:
        for pid, record in tracked.items():
            print('Force-stopping installer process %d.' % pid, flush=True)
            signal_same_process(pid, record, signal.SIGKILL)
    if bootout is not None:
        output, _ = bootout.communicate(timeout=10)
        require(bootout.returncode == 0, 'Could not unload installer helper: ' + output.decode(errors='replace'))
    # SIGKILL is immediate; allow only a short bounded interval for kernel fd cleanup.
    for _ in range(20):
        snapshot = processes()
        survivors = {pid for pid, record in tracked.items()
                     if pid in snapshot and snapshot[pid][1:] == record[1:]}
        if not survivors and not installer_tree(snapshot):
            return registered
        time.sleep(0.1)
    raise RuntimeError('Installer processes did not release their resources; stopping.')


def authorize():
    password = getpass.getpass('Admin password: ').encode() + b'\n'
    require(password != b'\n', 'No password entered.')
    # Pass the same password to sudo and, if needed, bless through private stdin pipes.
    # It never goes in arguments, environment variables or a file.
    subprocess.run(['/usr/bin/sudo', '-k'], check=True, stdin=subprocess.DEVNULL)
    result = subprocess.run(['/usr/bin/sudo', '-S', '-p', '', '-v'], input=password,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    require(result.returncode == 0, 'Administrator authentication failed. Run the script again to retry.')
    return subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-u', __file__, '--authorized'],
                          input=password).returncode


def erase_partition(plan, removed, target):
    for attempt in range(3):
        current = check_remaining(plan, removed)
        require(default_is_macos(plan), 'Default startup disk changed; stopping.')
        device = current[target['DiskUUID']]['DeviceIdentifier']
        args = (DISKUTIL, 'apfs', 'deleteContainer', device) if target['Content'] == 'Apple_APFS' else (
            DISKUTIL, 'eraseVolume', 'free', 'free', device)
        try:
            run(*args)
            return
        except subprocess.CalledProcessError as error:
            output = error.output or b''
            if attempt == 2 or not any(code in output for code in (b'-69879', b'-69888')):
                raise
            check_remaining(plan, removed)
            print('Disk is releasing its handles; retrying.', flush=True)
            time.sleep(0.5)


def execute(plan, user, password=None):
    check_remaining(plan)
    if not default_is_macos(plan):
        print('Selecting this macOS as the default startup disk.', flush=True)
        run('/usr/sbin/bless', '--mount', '/', '--setBoot', '--user', user,
            '--stdinpass', password=password)
    require(default_is_macos(plan),
            'Choose this macOS in System Settings > General > Startup Disk, then try again.')
    removed = []
    # Delete root, boot and ESP first; deleteContainer then removes the stub
    # partition itself on current macOS. Always resolve fresh device numbers.
    for target in reversed(plan['remove']):
        erase_partition(plan, removed, target)
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
    authorized = sys.argv[1:] == ['--authorized']
    require(check_only or authorized or not sys.argv[1:], 'Unknown argument.')
    require(sys.platform == 'darwin', 'This script requires macOS.')
    require(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.model']).strip() == b'Mac16,12',
            'This private uninstaller supports the J713 MacBook Air M4 only.')
    if not check_only and not authorized:
        print('This permanently removes Omarchy and expands macOS.\n'
              'Any running Omarchy installation will be stopped immediately.', flush=True)
        sys.exit(authorize())
    password = None
    if authorized:
        require(os.geteuid() == 0, 'Administrator privileges required.')
        password = sys.stdin.buffer.readline()
        require(password.endswith(b'\n') and password != b'\n', 'Missing administrator password.')
    if check_only:
        show_plan(make_plan(*inventory()))
        print('CHECK ONLY: no process, startup-disk, partition or filesystem changes.', flush=True)
        return
    user = os.environ.get('SUDO_USER', '')
    require(user and user != 'root', 'Launch this script from your macOS user account.')
    restart_helper = subprocess.run(['/bin/launchctl', 'print', HELPER_SERVICE],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    try:
        stop_installer()
        # Cancellation may have changed the layout. Discover it after stopping writers.
        plan = make_plan(*inventory())
        show_plan(plan)
        if not plan['remove'] and plan['macos']['IOKitSize'] >= plan['maximum_macos_bytes'] - 1024 * 1024:
            print('Already reset. Nothing to do.', flush=True)
            return
        execute(plan, user, password)
    finally:
        if restart_helper:
            result = subprocess.run(['/bin/launchctl', 'bootstrap', 'system', HELPER_PLIST],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            if result.returncode:
                print('Installer helper could not restart. Reopen/install the installer before reinstalling.',
                      flush=True)


def show_plan(plan):
    print('Omarchy partition reset\n', flush=True)
    for record, label in zip(plan['remove'], ('Omarchy APFS stub', 'Omarchy EFI', 'Linux boot', 'Linux root')):
        print('Remove: %-20s %8.2f GB  %s' % (label, record['IOKitSize'] / 1e9, record['DiskUUID']), flush=True)
    print('Expand macOS to approximately %.2f GB.' % (plan['maximum_macos_bytes'] / 1e9), flush=True)
    print('Preserve: running macOS, Apple ISC and System Recovery.', flush=True)



if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, StopIteration, OSError, EOFError, KeyboardInterrupt) as error:
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
/usr/bin/python3 -u "$work/uninstall.py" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
set -e
echo
exit "$status"
