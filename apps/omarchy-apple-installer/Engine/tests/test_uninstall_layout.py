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


class UninstallTests(unittest.TestCase):
    def test_plan_discovers_all_four_partitions_without_pinned_device_numbers(self):
        plan = worker.make_plan(*fixture())
        self.assertEqual([p['DiskUUID'] for p in plan['remove']],
                         ['partition-3', 'partition-4', 'partition-5', 'partition-6'])
        self.assertNotIn('DeviceIdentifier', plan['macos'])

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

        def run(*args):
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
