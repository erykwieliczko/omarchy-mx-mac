"""Boot APFS sizing is independent of the user's Linux allocation."""
import io
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

CLEANROOM = Path(__file__).resolve().parents[1] / "cleanroom"
sys.path.insert(0, str(CLEANROOM))
from boot_inputs import BootInputError
from boot_space import check_prepared_space, check_installed_space, STUB_SIZE, RECOVERY_FREE_BYTES, BOOT_OVERHEAD_BYTES


class BootSpaceTests(unittest.TestCase):
    def test_decoded_recovery_replaces_temporary_encrypted_bytes(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("BaseSystem.dmg.aea", b"encrypted")
            archive.writestr("boot-object", b"boot")
        receipt = {"input": {"member": "BaseSystem.dmg.aea"}, "decoded": {"size_bytes": 1560907597}}
        with zipfile.ZipFile(output) as archive, patch("boot_space.stub_members", return_value=archive.namelist()):
            expected = 1560907597 + 4 + BOOT_OVERHEAD_BYTES + RECOVERY_FREE_BYTES
            self.assertEqual(check_prepared_space(archive, {}, receipt, expected), expected)
            with self.assertRaisesRegex(BootInputError, "no partitions have changed"):
                check_prepared_space(archive, {}, receipt, expected - 1)

    def test_known_neo_footprint_fits_new_size_but_not_old_size(self):
        # Measured selected25F84 non-Recovery bytes and decoded DMG from the
        # qualified source, plus the explicit allowances (not compressed size).
        required = 397541632 + 1560907597 + BOOT_OVERHEAD_BYTES + RECOVERY_FREE_BYTES
        self.assertGreater(required, 2499805184)
        self.assertLess(required, STUB_SIZE)
        self.assertGreater(STUB_SIZE - 2470105088, RECOVERY_FREE_BYTES)

    def test_actual_space_checks_the_installed_container_and_boundary(self):
        for free, accepted in ((29700096, False), (RECOVERY_FREE_BYTES - 1, False),
                               (RECOVERY_FREE_BYTES, True), (STUB_SIZE, True)):
            with patch("boot_space.shutil.disk_usage", return_value=SimpleNamespace(free=free)) as usage:
                if accepted:
                    check_installed_space("/Volumes/Omarchy")
                else:
                    with self.assertRaisesRegex(BootInputError, "Apple boot container"):
                        check_installed_space("/Volumes/Omarchy")
                usage.assert_called_once_with("/Volumes/Omarchy")

    def test_recovery_shell_gate_rejects_full_and_unreadable_containers(self):
        script = (CLEANROOM / "step2.sh").read_text()
        function = re.search(r"check_boot_space\(\) \{.*?\n\}", script, re.S).group(0)
        for free, accepted in (("29004", False), ("1048575", False), ("1048576", True), ("unknown", False)):
            harness = '\n'.join((
                'set -euo pipefail', 'system="/Volumes/Omarchy"',
                'recovery_free_kib=' + str(RECOVERY_FREE_BYTES // 1024),
                'fail() { echo "$*"; exit 1; }',
                'function /bin/df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\n"; '
                + 'printf "disk 4194304 1 ' + free + ' 1%% /Volumes/Omarchy\\n"; }',
                function, 'check_boot_space', 'echo passed'))
            result = subprocess.run(["/bin/bash", "-c", harness], capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, accepted, result.stderr + result.stdout)
            self.assertEqual("passed" in result.stdout, accepted)
