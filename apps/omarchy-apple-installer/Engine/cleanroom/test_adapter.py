# SPDX-License-Identifier: MIT
"""Run against stage_sources.py output; every target is a temporary file."""

import copy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from adapter import CleanroomStage1Adapter, FIRMWARE_NAMES, cleanroom_spec, restore_layout
from boot_inputs import BootInputError, assemble_stage1, load_profile
from main import CleanroomInstaller, CleanroomRuntime
from firmware import collect_macos_wifi, normalize_nvram


PROFILE_PATH = Path(__file__).resolve().parent / "profiles/j713.json"
ESP = "11111111-2222-4333-8444-555555555555"
VGID = "22222222-3333-4444-8555-666666666666"


class CleanroomAdapterTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILE_PATH)

    def test_model_gate_requires_complete_identity_and_macos_baseline(self):
        installer = object.__new__(CleanroomInstaller)
        installer.cleanroom_profile = self.profile
        installer.engine_runtime = SimpleNamespace(mode="install")
        values = {key: self.profile[key] for key in
                  ("product_type", "device_class", "board_id", "chip_id")}
        values.update(boot_mode="macOS", macos_ver="26.6.2")
        installer.sysinfo = SimpleNamespace(**values)
        self.assertTrue(installer.host_supported())
        for key, value in (("product_type", "Mac16,1"), ("device_class", "j614sap"),
                           ("board_id", 45), ("chip_id", 0x8133),
                           ("boot_mode", "one true recoveryOS"), ("macos_ver", "26.6.1")):
            with self.subTest(key=key):
                installer.sysinfo = SimpleNamespace(**(values | {key: value}))
                self.assertFalse(installer.host_supported())

    def test_unknown_boot_policy_is_only_allowed_for_unprivileged_inventory(self):
        installer = object.__new__(CleanroomInstaller)
        installer.cleanroom_profile = self.profile
        installer.sysinfo = SimpleNamespace(**{key: self.profile[key] for key in
            ("product_type", "device_class", "board_id", "chip_id")},
            boot_mode="Unknown", macos_ver="26.6.2")
        for uid, mode, accepted in ((501, "inspect", True), (501, "plan", True),
                                    (0, "inspect", False), (0, "install", False),
                                    (501, "install", False)):
            installer.engine_runtime = SimpleNamespace(mode=mode)
            with patch("main.os.geteuid", return_value=uid):
                self.assertEqual(installer.host_supported(), accepted)

    def test_generic_macos_firmware_requires_exact_baseline_and_contained_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "System/Library/CoreServices/SystemVersion.plist"
            version.parent.mkdir(parents=True)
            version.write_bytes(plistlib.dumps({"ProductVersion": "26.6.2", "ProductBuildVersion": "25G83"}))
            source = root / self.profile["wifi"]["source_directory"]
            source.mkdir(parents=True)
            for name in self.profile["wifi"]["files"].values():
                (source / name).write_bytes(b" key =value\n\n" if name.endswith(".txt") else b"opaque generic firmware")
            collected = dict(collect_macos_wifi(self.profile, root))
            self.assertEqual(set(collected), set(self.profile["wifi"]["files"]))
            self.assertEqual(normalize_nvram(b" key =value\n\n"), b"key=value\n")
            version.write_bytes(plistlib.dumps({"ProductVersion": "26.6.2", "ProductBuildVersion": "25G82"}))
            with self.assertRaisesRegex(BootInputError, "baseline"):
                collect_macos_wifi(self.profile, root)
            version.write_bytes(plistlib.dumps({"ProductVersion": "26.6.2", "ProductBuildVersion": "25G83"}))
            entry = source / next(iter(self.profile["wifi"]["files"].values()))
            entry.unlink()
            entry.symlink_to(version)
            with self.assertRaisesRegex(BootInputError, "escapes"):
                collect_macos_wifi(self.profile, root)

    def test_metadata_requires_exact_firmware_and_source_graph(self):
        spec = {"schema_version": 1, "device_identifier": "apple,j713",
                "firmware_build": "25G83", "sources": self.profile["sources"],
                "restore_package": {"size_bytes": 10, "sha256": "a" * 64},
                "stage1": {"size_bytes": 4096, "sha256": "b" * 64},
                "linux_firmware": {name: "c" * 64 for name in FIRMWARE_NAMES}}
        self.assertEqual(cleanroom_spec({"os_list": [{"cleanroom": spec}]}, self.profile), spec)
        for mutation in (lambda s: s["linux_firmware"].pop("apple/tpmtfw-j713.bin"),
                         lambda s: s["sources"].update(linux="a" * 40),
                         lambda s: s["stage1"].update(size_bytes=True)):
            modified = copy.deepcopy(spec)
            mutation(modified)
            with self.assertRaises(BootInputError):
                cleanroom_spec({"os_list": [{"cleanroom": modified}]}, self.profile)

    def test_unknown_restore_layout_fails_before_stub_allocation(self):
        for bless, accepted in (({"Version": 1, "SupportsPairedRecovery": True}, True),
                                ({"Version": 2, "SupportsPairedRecovery": True}, False),
                                ({"Version": 1, "SupportsPairedRecovery": False}, False)):
            buffer = io.BytesIO()
            original = plistlib.dumps({"bless2": bless})
            with zipfile.ZipFile(buffer, "w") as writer:
                writer.writestr("usr/standalone/bootcaches.plist", original)
            with zipfile.ZipFile(buffer) as archive:
                if accepted:
                    self.assertEqual(restore_layout(archive, self.profile)["bless2"]["RestoreBundlePath"],
                                     "restore")
                    self.assertEqual(archive.read("usr/standalone/bootcaches.plist"), original)
                else:
                    with self.assertRaises(BootInputError):
                        restore_layout(archive, self.profile)

    def test_engine_cannot_enter_interactive_or_repair_mode(self):
        with self.assertRaisesRegex(ValueError, "authenticated engine mode"):
            CleanroomInstaller("test", engine_runtime=None)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "fresh installation"):
                CleanroomRuntime(mode="inspect", values={
                    "OMARCHY_ENGINE_JOURNAL": str(Path(directory) / "journal.json"),
                    "OMARCHY_ENGINE_REPAIR_MANIFEST": "repair.json"})

    def test_installed_boot_inputs_reject_changed_stage1_recovery_and_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "base.bin"
            raw.write_bytes(b"M" * 4096)
            expected = assemble_stage1(raw.read_bytes(),
                                       expected_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                                       esp_uuid=ESP)
            resources = root / "system/Finish Installation.app/Contents/Resources"
            resources.mkdir(parents=True)
            stage1 = resources / "boot.bin"
            stage1.write_bytes(expected)
            recovery = root / "recovery" / VGID / "usr/standalone/firmware/arm64eBaseSystem.dmg"
            recovery.parent.mkdir(parents=True)
            recovery.write_bytes(b"verified recovery")
            restore = root / "preboot" / VGID / "restore"
            (restore / "Firmware").mkdir(parents=True)
            companion = restore / "Firmware/base.root_hash"
            companion.write_bytes(b"Apple authentication")
            manifest = {"BuildIdentities": [{"Info": {"DeviceClass": "j713ap"}}]}
            (restore / "BuildManifest.plist").write_bytes(plistlib.dumps(manifest))
            stub = SimpleNamespace(get_paths=lambda: None, boot_obj_path=stage1,
                                   step2_sh=resources / "step2.sh", pb_vgid=restore.parent,
                                   osi=SimpleNamespace(recovery=root / "recovery", vgid=VGID))
            adapter = object.__new__(CleanroomStage1Adapter)
            adapter.profile = self.profile
            adapter.installer = SimpleNamespace(ins=stub)
            adapter.osins = SimpleNamespace(efi_part=SimpleNamespace(uuid=ESP))
            adapter.stage1_path = raw
            adapter.verifier_path = root / "verifier"
            adapter.verifier_path.write_bytes(b"admitted native verifier")
            verifier = resources / "omarchy-restore-image"
            verifier.write_bytes(adapter.verifier_path.read_bytes())
            adapter.spec = {"stage1": {"sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}}
            descriptor = lambda p: {"size_bytes": p.stat().st_size,
                                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            adapter.recovery_receipt = {"decoded": descriptor(recovery), "authentication": {
                "root_hash": {"member": "Firmware/base.root_hash", **descriptor(companion)}}}
            adapter.selection = {"manifest": manifest}
            with patch.object(adapter, "_recovery_script", return_value="bound script\n"):
                stub.step2_sh.write_text("bound script\n")
                self.assertIn("cleanroom_boot_inputs", adapter.boot_input_evidence())
                for path in (stage1, recovery, companion, stub.step2_sh, verifier,
                             restore / "BuildManifest.plist"):
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed")
                    with self.subTest(path=path.name), self.assertRaises((ValueError, plistlib.InvalidFileException)):
                        adapter.boot_input_evidence()
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
