"""Exercise the standalone reset script without access to any physical disk."""
import copy
import io
from pathlib import Path
import subprocess
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'Packaging/uninstall-omarchy.command'
worker = types.ModuleType('uninstall_worker')
exec(compile(SCRIPT.read_text().split("<<'PYTHON'\n", 1)[1].split('\nPYTHON\n', 1)[0],
             str(SCRIPT), 'exec'), worker.__dict__)


def fixture():
    kinds = ['Apple_APFS_ISC', 'Apple_APFS', 'Apple_APFS', 'EFI',
             'Linux Filesystem', 'Linux Filesystem', 'Apple_APFS_Recovery']
    sizes = [524288000, 110 * 1024**3, 2499805184, 524288000,
             2147483648, 100 * 1024**3, 5368664064]
    records = []
    offset = 24576
    for index, (kind, size) in enumerate(zip(kinds, sizes), 1):
        records.append({'Content': kind, 'IOKitSize': size, 'DiskUUID': 'partition-' + str(index),
                        'ParentWholeDisk': 'disk9', 'DeviceIdentifier': 'disk9s' + str(index),
                        'PartitionMapPartitionOffset': offset, 'Internal': True,
                        'PartitionMapPartition': True})
        offset += size
    records[3]['VolumeName'] = 'EFI - OMARC'
    root = {'APFSVolumeGroupID': 'mac-group', 'APFSContainerReference': 'disk20'}
    containers = [{'ContainerReference': 'disk30',
                   'PhysicalStores': [{'DeviceIdentifier': 'disk9s3'}],
                   'Volumes': [{'Roles': [role], 'Name': name} for role, name in (
                       ('System', 'Omarchy'), ('Data', 'Omarchy - Data'),
                       ('Preboot', 'Preboot'), ('Recovery', 'Recovery'))]}]
    return root, 'disk9s2', records, containers


def j700_fixture():
    root, store, records, containers = fixture()
    records.pop(4)
    records[2]['IOKitSize'] = 8000000000
    records[3]['IOKitSize'] = 999997440
    records[3]['VolumeName'] = 'EFI-M1N1'
    offset = 24576
    for record in records:
        record['PartitionMapPartitionOffset'] = offset
        offset += record['IOKitSize']
    records[-1]['PartitionMapPartitionOffset'] += 128 * 1024**2
    containers[0]['Volumes'][0]['Name'] = 'm1n1 J700'
    containers[0]['Volumes'][1]['Name'] = 'm1n1 J700 - Data'
    return root, store, records, containers


class UninstallTests(unittest.TestCase):
    def test_plan_discovers_all_four_partitions_without_pinned_device_numbers(self):
        plan = worker.make_plan(*fixture())
        self.assertEqual([p['DiskUUID'] for p in plan['remove']],
                         ['partition-3', 'partition-4', 'partition-5', 'partition-6'])
        self.assertNotIn('DeviceIdentifier', plan['macos'])

    def test_j700_plan_removes_three_linux_partitions(self):
        plan = worker.make_plan(*j700_fixture())
        self.assertEqual([p['Content'] for p in plan['remove']],
                         ['Apple_APFS', 'EFI', 'Linux Filesystem'])
        with redirect_stdout(io.StringIO()) as output:
            worker.show_plan(plan)
        self.assertIn('Linux root', output.getvalue())
        self.assertNotIn('Linux boot', output.getvalue())

    def test_j700_rejects_another_stub_efi_or_changed_geometry(self):
        for case in ('name', 'efi', 'size', 'extra-volume', 'gap'):
            root, store, records, containers = j700_fixture()
            if case == 'name':
                containers[0]['Volumes'][0]['Name'] = 'Another macOS'
            elif case == 'efi':
                records[3]['VolumeName'] = 'EFI - FEDORA'
            elif case == 'size':
                records[2]['IOKitSize'] -= 4096
            elif case == 'extra-volume':
                containers[0]['Volumes'].append({'Roles': [], 'Name': 'Important data'})
            else:
                records[-1]['PartitionMapPartitionOffset'] += 2 * 1024**2
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                worker.make_plan(root, store, records, containers)

    def test_unknown_or_ambiguous_layouts_are_rejected(self):
        for case in ('extra', 'booted-stub', 'external', 'wrong-label', 'wrong-size',
                     'another-os', 'multiple-stores', 'wrong-order', 'overlap'):
            root, store, records, containers = fixture()
            if case == 'extra':
                records.append(copy.deepcopy(records[-1]))
            elif case == 'booted-stub':
                store = 'disk9s3'
            elif case == 'external':
                records[5]['Internal'] = False
            elif case == 'wrong-label':
                records[3]['VolumeName'] = 'EFI - FEDORA'
            elif case == 'wrong-size':
                records[4]['IOKitSize'] //= 2
            elif case == 'another-os':
                containers[0]['Volumes'][0]['Name'] = 'Other macOS'
            elif case == 'multiple-stores':
                containers[0]['PhysicalStores'].append({'DeviceIdentifier': 'disk10s3'})
            elif case == 'wrong-order':
                records[4]['Content'] = 'Apple_APFS'
            elif case == 'overlap':
                records[5]['PartitionMapPartitionOffset'] -= 4096
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                worker.make_plan(root, store, records, containers)

    def test_existing_clean_layout_is_recognized(self):
        root, store, records, containers = fixture()
        self.assertEqual(worker.make_plan(root, store, records[:2] + records[-1:], containers)['remove'], [])

    def test_delete_sequence_resolves_renumbered_devices_and_preserves_apple_partitions(self):
        root, store, records, containers = fixture()
        plan = worker.make_plan(root, store, records, containers)
        original = copy.deepcopy(records)
        calls = []
        startup = [False]

        def inventory():
            return root, records[1]['DeviceIdentifier'], records, containers

        def run(*args, **kwargs):
            calls.append(args)
            if args[0] == '/usr/sbin/bless':
                startup[0] = True
                return
            if args[1:3] == ('apfs', 'resizeContainer'):
                self.assertEqual(args[3], records[1]['DeviceIdentifier'])
                records[1]['IOKitSize'] = plan['maximum_macos_bytes']
                return
            device = args[-1]
            target = next(p for p in records if p['DeviceIdentifier'] == device)
            self.assertIn(target['DiskUUID'], [p['DiskUUID'] for p in plan['remove']])
            records.remove(target)
            # Disk identifiers are transient; change every surviving identifier.
            for i, record in enumerate(records):
                record['DeviceIdentifier'] = 'disk9s' + str(i + 20 * len(calls))

        with patch.object(worker, 'inventory', inventory), patch.object(worker, 'run', run), \
                patch.object(worker, 'default_is_macos', lambda _: startup[0]), redirect_stdout(io.StringIO()):
            worker.execute(plan, 'test-owner')
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0][0], '/usr/sbin/bless')
        self.assertEqual(calls[-2][1:3], ('apfs', 'deleteContainer'))
        self.assertEqual([p['DiskUUID'] for p in records], ['partition-1', 'partition-2', 'partition-7'])
        for before, after in ((original[0], records[0]), (original[-1], records[-1])):
            self.assertEqual(worker.partition(before), worker.partition(after))

    def test_startup_selection_failure_never_reaches_erase(self):
        data = fixture()
        plan = worker.make_plan(*data)
        with patch.object(worker, 'inventory', return_value=data), \
                patch.object(worker, 'default_is_macos', return_value=False), \
                patch.object(worker, 'run', side_effect=subprocess.CalledProcessError(1, 'bless')) as run, \
                redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            worker.execute(plan, 'test-owner')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], '/usr/sbin/bless')

    def test_changed_partition_or_booted_group_stops_before_any_mutation(self):
        for change in ('uuid', 'group', 'offset'):
            data = fixture()
            plan = worker.make_plan(*data)
            if change == 'uuid':
                data[2][5]['DiskUUID'] = 'replacement-disk'
            elif change == 'group':
                data[0]['APFSVolumeGroupID'] = 'another-mac'
            else:
                data[2][1]['PartitionMapPartitionOffset'] += 4096
            with self.subTest(change=change), patch.object(worker, 'inventory', return_value=data), \
                    patch.object(worker, 'run') as run, self.assertRaises(RuntimeError):
                worker.execute(plan, 'test-owner')
            run.assert_not_called()

    def test_process_tree_includes_children_but_not_unrelated_python(self):
        app = worker.APP + 'MacOS/OmarchyAppleInstallerApp'
        snapshot = {10: (1, 'start', app), 11: (10, 'start', '/bin/sh'),
                    12: (11, 'start', '/usr/bin/python3'),
                    20: (1, 'start', '/usr/bin/python3'),
                    30: (1, 'start', '/private/var/db/com.omarchy.mx.installer/engine-execution-id/bundle/Python')}
        self.assertEqual(set(worker.installer_tree(snapshot)), {10, 11, 12, 30})

    def test_pid_reuse_is_not_killed(self):
        old = (1, 'old-start', '/installer')
        with patch.object(worker, 'processes', return_value={10: (1, 'new-start', '/installer')}), \
                patch.object(worker.os, 'kill') as kill:
            worker.signal_same_process(10, old, worker.signal.SIGKILL)
        kill.assert_not_called()

    def test_immediate_kill_of_installer_tree(self):
        snapshot = {10: (1, 'start', worker.APP + 'Resources/omarchy-apple-installer-helper'),
                    11: (10, 'start', '/engine'), 20: (1, 'start', '/unrelated')}
        signals = []

        def send(pid, record, sig):
            signals.append((pid, sig))
            if sig == worker.signal.SIGKILL:
                snapshot.pop(pid, None)

        with patch.object(worker, 'processes', side_effect=lambda: dict(snapshot)), \
                patch.object(worker, 'signal_same_process', side_effect=send), \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)), \
                patch.object(worker.subprocess, 'Popen') as popen, \
                patch.object(worker.time, 'sleep') as sleep, redirect_stdout(io.StringIO()):
            popen.return_value.communicate.return_value = (b'', None)
            popen.return_value.returncode = 0
            self.assertTrue(worker.stop_installer())
        self.assertEqual({pid for pid, sig in signals if sig == worker.signal.SIGKILL}, {10, 11})
        self.assertNotIn(worker.signal.SIGTERM, [sig for pid, sig in signals])
        self.assertEqual(set(snapshot), {20})
        sleep.assert_not_called()
        self.assertEqual(popen.call_args.args[0], ['/bin/launchctl', 'bootout', worker.HELPER_SERVICE])

    def test_single_password_goes_only_through_stdin(self):
        worker.__file__ = '/temporary/uninstall.py'
        with patch.object(worker.getpass, 'getpass', return_value='test-secret') as prompt, \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertEqual(worker.authorize(), 0)
        prompt.assert_called_once()
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list[1:]:
            self.assertEqual(call.kwargs['input'], b'test-secret\n')
            self.assertNotIn('test-secret', repr(call.args))
        self.assertIn('-n', run.call_args_list[2].args[0])

    def test_bad_password_never_starts_privileged_worker(self):
        with patch.object(worker.getpass, 'getpass', return_value='wrong'), \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)) as run, \
                self.assertRaises(RuntimeError):
            worker.authorize()
        self.assertEqual(run.call_count, 2)

    def test_busy_disk_retries_with_fresh_device_identifier(self):
        data = fixture()
        plan = worker.make_plan(*data)
        calls = []

        def run(*args):
            calls.append(args)
            if len(calls) == 1:
                data[2][5]['DeviceIdentifier'] = 'disk9s88'
                raise subprocess.CalledProcessError(1, args, output=b"Error: -69879: Couldn't open disk")

        with patch.object(worker, 'inventory', return_value=data), \
                patch.object(worker, 'default_is_macos', return_value=True), \
                patch.object(worker, 'run', side_effect=run), \
                patch.object(worker.time, 'sleep'), redirect_stdout(io.StringIO()):
            worker.erase_partition(plan, [], plan['remove'][-1])
        self.assertEqual([call[-1] for call in calls], ['disk9s6', 'disk9s88'])

    def test_busy_retry_stops_if_partition_identity_changes(self):
        data = fixture()
        plan = worker.make_plan(*data)

        def run(*args):
            data[2][5]['DiskUUID'] = 'different-partition'
            raise subprocess.CalledProcessError(1, args, output=b'-69879')

        with patch.object(worker, 'inventory', return_value=data), \
                patch.object(worker, 'default_is_macos', return_value=True), \
                patch.object(worker, 'run', side_effect=run) as command, self.assertRaises(RuntimeError):
            worker.erase_partition(plan, [], plan['remove'][-1])
        self.assertEqual(command.call_count, 1)

    def test_startup_password_is_redacted_in_command_output(self):
        with patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(
                returncode=1, stdout=b'bad secret password')), redirect_stdout(io.StringIO()) as log, \
                self.assertRaises(subprocess.CalledProcessError) as raised:
            worker.run('/usr/sbin/bless', '--stdinpass', password=b'secret\n')
        self.assertNotIn('secret', log.getvalue())
        self.assertNotIn(b'secret', raised.exception.output)

    def test_check_mode_never_authorizes_or_stops_processes(self):
        with patch.object(worker.sys, 'argv', ['uninstall.py', '--check']), \
                patch.object(worker.sys, 'platform', 'darwin'), \
                patch.object(worker.subprocess, 'check_output', return_value=b'Mac16,12'), \
                patch.object(worker, 'inventory', return_value=fixture()), \
                patch.object(worker, 'authorize') as auth, \
                patch.object(worker, 'stop_installer') as stop, redirect_stdout(io.StringIO()):
            worker.main()
        auth.assert_not_called()
        stop.assert_not_called()

    def test_authorized_run_stops_installer_before_reading_layout(self):
        actions = []

        def inventory():
            actions.append('inventory')
            return fixture()

        with patch.object(worker.sys, 'argv', ['uninstall.py', '--authorized']), \
                patch.object(worker.sys, 'platform', 'darwin'), \
                patch.object(worker.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(b'secret\n'))), \
                patch.object(worker.os, 'geteuid', return_value=0), \
                patch.dict(worker.os.environ, {'SUDO_USER': 'test-owner'}), \
                patch.object(worker.subprocess, 'check_output', return_value=b'Mac16,12'), \
                patch.object(worker.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)), \
                patch.object(worker, 'inventory', side_effect=inventory), \
                patch.object(worker, 'stop_installer', side_effect=lambda: actions.append('stop')), \
                patch.object(worker, 'execute') as execute, redirect_stdout(io.StringIO()):
            worker.main()
        self.assertEqual(actions, ['stop', 'inventory'])
        self.assertEqual(execute.call_args.args[1:], ('test-owner', b'secret\n'))
