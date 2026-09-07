# SPDX-License-Identifier: MIT
"""Run against stage_sources.py output; every target is a temporary file."""

import copy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from adapter import CleanroomStage1Adapter, FIRMWARE_NAMES, cleanroom_spec, restore_layout
from boot_inputs import BootInputError, assemble_stage1, load_profile
from main import CleanroomInstaller, CleanroomRuntime
from firmware import collect_macos_wifi, normalize_nvram
from firmware_ranges import FirmwareRangeFallback, FirmwareRangeExecutionError


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

    def test_override_admits_different_model_and_host_version_but_requires_macos(self):
        installer = object.__new__(CleanroomInstaller)
        installer.cleanroom_profile = self.profile
        installer.engine_runtime = SimpleNamespace(mode="install", developer_model_override="apple,j713")
        installer.sysinfo = SimpleNamespace(product_type="Mac99,1", device_class="j999ap",
                                            board_id=99, chip_id=99, boot_mode="macOS", macos_ver="26.5.2")
        self.assertTrue(installer.host_supported())
        installer.sysinfo.boot_mode = "one true recoveryOS"
        self.assertFalse(installer.host_supported())
        installer.sysinfo.boot_mode = "macOS"
        installer.engine_runtime.developer_model_override = None
        self.assertFalse(installer.host_supported())

    def test_override_keeps_recovery_and_root_unknown_boot_modes_blocked(self):
        installer = object.__new__(CleanroomInstaller)
        installer.cleanroom_profile = self.profile
        installer.sysinfo = SimpleNamespace(product_type="Mac17,5", device_class="j700ap",
                                            board_id=1, chip_id=1, macos_ver="26.5.2")
        installer.engine_runtime = SimpleNamespace(mode="inspect", developer_model_override="apple,j713")
        for uid, mode, boot_mode, accepted in (
                (502, "inspect", "Unknown", True), (502, "plan", "Unknown", True),
                (0, "inspect", "Unknown", False), (0, "install", "Unknown", False),
                (0, "install", "macOS", True), (0, "install", "one true recoveryOS", False)):
            installer.sysinfo.boot_mode = boot_mode
            installer.engine_runtime.mode = mode
            with self.subTest(uid=uid, mode=mode, boot_mode=boot_mode), patch("main.os.geteuid", return_value=uid):
                self.assertEqual(installer.host_supported(), accepted)

    def test_override_inspection_normalizes_selected_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = CleanroomRuntime.from_environment({"OMARCHY_ENGINE_MODE": "inspect",
                "OMARCHY_ENGINE_JOURNAL": str(Path(directory) / "journal"),
                "OMARCHY_DEVELOPER_MODEL_OVERRIDE": "apple,j713"})
            runtime.inspect("j999ap", True)
            self.assertEqual(runtime.device_identifier, "apple,j713")

    def test_override_requires_matching_planning_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / "identity.json"
            values = {"schema_version": 1, "engine_version": "v1", "engine_digest": "sha256:" + "a" * 64,
                      "metadata_digest": "sha256:" + "b" * 64, "payload_digest": "sha256:" + "c" * 64}
            identity.write_text(json.dumps(values))
            env = {"OMARCHY_ENGINE_MODE": "plan", "OMARCHY_ENGINE_JOURNAL": str(Path(directory) / "journal"),
                   "OMARCHY_ENGINE_IDENTITY": str(identity), "OMARCHY_ENGINE_REQUEST": "request.json",
                   "OMARCHY_DEVELOPER_MODEL_OVERRIDE": "apple,j713"}
            with self.assertRaisesRegex(ValueError, "authenticated identity"):
                CleanroomRuntime.from_environment(env)
            values["developer_model_override"] = "apple,j713"
            identity.write_text(json.dumps(values))
            self.assertEqual(CleanroomRuntime.from_environment(env).developer_model_override, "apple,j713")
            del env["OMARCHY_DEVELOPER_MODEL_OVERRIDE"]
            with self.assertRaisesRegex(ValueError, "authenticated identity"):
                CleanroomRuntime.from_environment(env)

    def test_override_recovery_script_uses_actual_product(self):
        adapter = object.__new__(CleanroomStage1Adapter)
        adapter.profile = self.profile
        adapter.installer = SimpleNamespace(engine_runtime=SimpleNamespace(developer_model_override="apple,j713"),
            sysinfo=SimpleNamespace(product_type="Mac99,1"), ins=SimpleNamespace(osi=SimpleNamespace(vgid=VGID)))
        with patch("adapter.Path.read_text", return_value="PRODUCT=##PRODUCT## VGID=##VGID##"):
            self.assertIn("PRODUCT=Mac99,1", adapter._recovery_script(ESP, b"m1n1"))
            adapter.installer.sysinfo.product_type = "bad;command"
            with self.assertRaises(BootInputError):
                adapter._recovery_script(ESP, b"m1n1")

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
        apple = json.loads(PROFILE_PATH.with_name("j713-apple-inputs.json").read_text())
        spec = {"schema_version": 2, "device_identifier": "apple,j713",
                "firmware_build": "25G83", "sources": self.profile["sources"],
                "apple_inputs": apple,
                "apple_boot_builds": json.loads(PROFILE_PATH.with_name("apple-boot-builds.json").read_text()),
                "stage1": {"size_bytes": 4096, "sha256": "b" * 64},
                "linux_firmware": apple["linux_firmware"]}
        with patch("adapter.load_apple_inputs", return_value=copy.deepcopy(apple)):
            self.assertEqual(cleanroom_spec({"os_list": [{"cleanroom": spec}]}, self.profile), spec)
            for mutation in (lambda s: s["linux_firmware"].pop("apple/tpmtfw-j713.bin"),
                             lambda s: s["sources"].update(linux="a" * 40),
                             lambda s: s["apple_inputs"]["ipsw"].update(url="https://example.org/input.ipsw"),
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

    def test_failed_apple_download_never_enters_partition_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "os.zip"
            with zipfile.ZipFile(payload, "w"):
                pass
            adapter = object.__new__(CleanroomStage1Adapter)
            adapter.profile = self.profile
            adapter.preflight_complete = False
            adapter.payload_path = payload
            adapter.metadata_path = Path(directory) / "metadata.json"
            adapter.installer = SimpleNamespace(sysinfo=SimpleNamespace(**{
                key: self.profile[key] for key in ("product_type", "device_class", "board_id", "chip_id")}, sfr_full_ver="25.7.83.0.0,0"))
            apple = json.loads(PROFILE_PATH.with_name("j713-apple-inputs.json").read_text())
            spec = {"stage1": {"sha256": "a" * 64, "size_bytes": 4096}, "apple_inputs": apple}
            plan = SimpleNamespace(candidate_kind="resize", device_identifier="apple,j713")
            for free_gib, message in ((128, "Apple download failed"), (16, "64 GiB")):
                with patch("adapter.load_metadata", return_value={}), \
                        patch("adapter.cleanroom_spec", return_value=spec), \
                        patch("adapter.file_descriptor", return_value=spec["stage1"]), \
                        patch("adapter.shutil.disk_usage", return_value=SimpleNamespace(free=free_gib * 1024**3)), \
                        patch("adapter.extract_wifi", return_value=[]), \
                        patch("adapter.selected_archive", side_effect=BootInputError("Apple download failed")) as download, \
                        patch("adapter.AsahiStage1Adapter.preflight") as transaction:
                    try:
                        with self.assertRaisesRegex(BootInputError, message):
                            adapter.preflight(plan)
                        if free_gib < 64:
                            download.assert_not_called()
                        transaction.assert_not_called()
                        self.assertFalse(adapter.preflight_complete)
                    finally:
                        adapter.workspace.cleanup()
            adapter.installer.engine_runtime = SimpleNamespace(developer_model_override="apple,j713")
            with patch("adapter.load_metadata", return_value={}), \
                    patch("adapter.cleanroom_spec", return_value=spec), \
                    patch("adapter.file_descriptor", return_value=spec["stage1"]), \
                    patch("adapter.shutil.disk_usage", return_value=SimpleNamespace(free=128 * 1024**3)), \
                    patch("adapter.extract_wifi", side_effect=FirmwareRangeExecutionError("cancelled")), \
                    patch("adapter.AsahiStage1Adapter.preflight") as transaction:
                try:
                    with self.assertRaisesRegex(FirmwareRangeExecutionError, "cancelled"):
                        adapter.preflight(plan)
                    transaction.assert_not_called()
                finally:
                    adapter.workspace.cleanup()

    def test_range_success_skips_system_mount_and_fallback_restores_it(self):
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        apple = json.loads(PROFILE_PATH.with_name("j713-apple-inputs.json").read_text())
        spec = {"stage1": {"sha256": "a" * 64, "size_bytes": 4096}, "apple_inputs": apple}
        for fallback, host_device, yolo, sfr in (
                (False, "j713ap", False, "25.7.83.0.0,0"), (True, "j713ap", False, "25.7.83.0.0,0"),
                (False, "j700ap", True, "25.7.83.0.0,0"), (True, "j700ap", True, "25.7.83.0.0,0"),
                (True, "j713ap", True, "25.7.83.0.0,0"), (False, "j700ap", True, "25.6.84.0.0,0"),
                (False, "j713ap", True, "25.6.84.0.0,0")):
            with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                payload = Path(directory) / "os.zip"
                with zipfile.ZipFile(payload, "w"):
                    pass
                adapter = object.__new__(CleanroomStage1Adapter)
                adapter.profile = self.profile
                adapter.preflight_complete = False
                adapter.payload_path = payload
                adapter.metadata_path = Path(directory) / "metadata.json"
                adapter.installer = SimpleNamespace(sysinfo=SimpleNamespace(**{
                    key: self.profile[key] for key in ("product_type", "device_class", "board_id", "chip_id")}, sfr_full_ver="25.7.83.0.0,0"))
                if host_device == "j700ap":
                    adapter.installer.sysinfo = SimpleNamespace(product_type="Mac17,5", device_class="j700ap",
                                                                board_id=100, chip_id=0x8140, sfr_full_ver="25.7.83.0.0,0")
                if yolo:
                    adapter.installer.engine_runtime = SimpleNamespace(developer_model_override="apple,j713")
                adapter.installer.sysinfo.sfr_full_ver = sfr
                mocks = {}
                values = {"load_metadata": {}, "cleanroom_spec": spec,
                          "file_descriptor": spec["stage1"], "selected_archive": payload,
                          "stub_members": [], "inspect_ipsw": {}, "restore_layout": {}, "verify_boot_version": None,
                          "prepare_recovery": {}, "collect_macos_wifi": [],
                          "retain_stub_inputs": payload, "verify_retained_workspace": None}
                for name, value in values.items():
                    mocks[name] = stack.enter_context(patch("adapter." + name, return_value=value))
                stack.enter_context(patch("adapter.shutil.disk_usage", return_value=SimpleNamespace(free=128 * 1024**3)))
                extraction = stack.enter_context(patch("adapter.extract_wifi", return_value=[]))
                if fallback:
                    extraction.side_effect = FirmwareRangeFallback("unavailable")
                mount = stack.enter_context(patch("adapter.mounted_system_image", return_value=MagicMock()))
                stack.enter_context(patch.object(adapter, "_collect_linux_firmware", return_value=[]))
                separate = stack.enter_context(patch.object(adapter, "_collect_separate_linux_firmware", return_value=[]))
                transaction = stack.enter_context(patch("adapter.AsahiStage1Adapter.preflight"))
                try:
                    adapter.preflight(SimpleNamespace(candidate_kind="resize", device_identifier="apple,j713"))
                    needs_fallback = fallback and not yolo
                    self.assertEqual(mocks["selected_archive"].call_args.kwargs["include_system"], needs_fallback)
                    if host_device != "j713ap":
                        extraction.assert_not_called()
                    self.assertEqual(adapter.boot_profile["device_class"], host_device)
                    self.assertEqual(mocks["selected_archive"].call_args.args[1]["device_class"], host_device)
                    self.assertEqual(mocks["prepare_recovery"].call_args.args[1]["device_class"], host_device)
                    self.assertEqual(mocks["retain_stub_inputs"].call_args.args[1]["device_class"], host_device)
                    self.assertEqual([call.args[1]["device_class"] for call in mocks["inspect_ipsw"].call_args_list],
                                     [host_device, "j713ap"] if host_device == "j713ap" and sfr == "25.7.83.0.0,0"
                                     else [host_device])
                    self.assertEqual(separate.call_count, int(host_device == "j713ap" and sfr == "25.6.84.0.0,0"))
                    self.assertEqual(adapter.installer.cleanroom_boot_profile["firmware"]["build"],
                                     "25F84" if sfr == "25.6.84.0.0,0" else "25G83")
                    if needs_fallback:
                        self.assertEqual(mount.call_args.args[2]["device_class"], "j713ap")
                    self.assertEqual(mount.call_count, int(needs_fallback))
                    self.assertEqual(mocks["collect_macos_wifi"].call_count, int(needs_fallback))
                    transaction.assert_called_once()
                finally:
                    adapter.workspace.cleanup()

    def test_sfr_failure_precedes_workspace_download_and_partition_operations(self):
        adapter = object.__new__(CleanroomStage1Adapter)
        adapter.profile = self.profile
        adapter.preflight_complete = False
        adapter.metadata_path = Path("metadata.json")
        host = SimpleNamespace(product_type="Mac17,5", device_class="j700ap", board_id=100, chip_id=0x8140)
        adapter.installer = SimpleNamespace(sysinfo=host,
            engine_runtime=SimpleNamespace(developer_model_override="apple,j713"))
        apple = json.loads(PROFILE_PATH.with_name("j713-apple-inputs.json").read_text())
        for value in (None, "25.6.83.0.0,0"):
            host.sfr_full_ver = value
            with patch("adapter.load_metadata", return_value={}), \
                    patch("adapter.cleanroom_spec", return_value={"apple_inputs": apple}), \
                    patch("adapter.AppleWorkspace") as workspace, \
                    patch("adapter.selected_archive") as download, \
                    patch("adapter.AsahiStage1Adapter.preflight") as transaction:
                with self.assertRaises(BootInputError):
                    adapter.preflight(SimpleNamespace(candidate_kind="resize", device_identifier="apple,j713"))
                workspace.assert_not_called()
                download.assert_not_called()
                transaction.assert_not_called()

    def test_choose_ipsw_uses_preflight_boot_version_and_retains_linux_metadata_gate(self):
        installer = object.__new__(CleanroomInstaller)
        installer.cleanroom_profile = self.profile
        with tempfile.TemporaryDirectory() as directory:
            installer.cleanroom_restore_path = Path(directory) / "boot.zip"
            installer.cleanroom_restore_path.touch()
            with self.assertRaisesRegex(BootInputError, "boot version"):
                installer.choose_ipsw(["26.6.2"])
            installer.cleanroom_boot_profile = {**self.profile, "firmware": {**self.profile["firmware"],
                                                                          "version": "26.5.2", "build": "25F84"}}
            self.assertEqual(installer.choose_ipsw(["26.6.2"]).version, "26.5.2")
            with self.assertRaisesRegex(BootInputError, "baseline"):
                installer.choose_ipsw(["26.5.2"])

    def test_optional_touchpad_failure_skips_but_cancellation_propagates(self):
        adapter = object.__new__(CleanroomStage1Adapter)
        adapter.profile = self.profile
        adapter.installer = SimpleNamespace(engine_runtime=SimpleNamespace(developer_model_override="apple,j713"))
        adapter.spec = {"apple_inputs": json.loads(PROFILE_PATH.with_name("j713-apple-inputs.json").read_text())}
        with tempfile.TemporaryDirectory() as directory:
            for error in (zipfile.BadZipFile("bad optional cache"), subprocess.CalledProcessError(1, "cp"),
                          subprocess.CalledProcessError(-15, "cp")):
                with patch("adapter.selected_files", side_effect=error), \
                        patch.object(adapter, "_collect_linux_firmware", return_value=[]) as collect:
                    if isinstance(error, subprocess.CalledProcessError) and error.returncode < 0:
                        with self.assertRaises(subprocess.CalledProcessError):
                            adapter._collect_separate_linux_firmware(Path(directory), [], None)
                        collect.assert_not_called()
                    else:
                        self.assertEqual(adapter._collect_separate_linux_firmware(Path(directory), [], None), [])
                        collect.assert_called_once()

    def test_yolo_optional_firmware_skips_missing_and_invalid_files(self):
        from asahi_firmware.core import FWFile, FWPackage
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            adapter = object.__new__(CleanroomStage1Adapter)
            adapter.profile = self.profile
            adapter.installer = SimpleNamespace(engine_runtime=SimpleNamespace(developer_model_override="apple,j713"))
            adapter.linux_selection = None
            good = FWFile("good", b"verified")
            adapter.spec = {"linux_firmware": {"brcm/good.bin": hashlib.sha256(good.data).hexdigest(),
                                               "brcm/missing.bin": "a" * 64}}
            self.assertEqual(adapter._collect_linux_firmware(None, work, []), [])
            result = adapter._collect_linux_firmware(None, work,
                [("brcm/good.bin", good), ("brcm/missing.bin", FWFile("bad", b"wrong bytes"))])
            self.assertEqual(result, [("brcm/good.bin", good)])
            # Even without device firmware, GRUB's vendor initramfs exists.
            package = FWPackage(directory)
            package.add_files([])
            package.close()
            for name in ("firmware.cpio", "firmware.tar", "manifest.txt"):
                self.assertTrue((work / name).is_file())
            adapter.installer.engine_runtime.developer_model_override = None
            with self.assertRaisesRegex(BootInputError, "firmware differs"):
                adapter._collect_linux_firmware(None, work, [])

    def test_yolo_missing_touchpad_firmware_keeps_verified_wifi(self):
        from asahi_firmware.core import FWFile
        adapter = object.__new__(CleanroomStage1Adapter)
        adapter.profile = self.profile
        adapter.installer = SimpleNamespace(engine_runtime=SimpleNamespace(developer_model_override="apple,j713"))
        adapter.linux_selection = {"manifest": {"BuildIdentities": [{"Manifest": {}}]}}
        wifi = FWFile("wifi", b"verified")
        adapter.spec = {"linux_firmware": {"brcm/wifi.bin": hashlib.sha256(wifi.data).hexdigest(),
                                          "apple/tpmtfw-j713.bin": "a" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(adapter._collect_linux_firmware(None, Path(directory), [("brcm/wifi.bin", wifi)]),
                             [("brcm/wifi.bin", wifi)])

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
            esp = root / "esp"
            (esp / "vendorfw").mkdir(parents=True)
            package = root / "firmware-package"
            package.mkdir()
            for name in ("firmware.cpio", "firmware.tar", "manifest.txt"):
                (package / name).write_bytes(b"verified " + name.encode())
                (esp / "vendorfw" / name).write_bytes((package / name).read_bytes())
            adapter.installer = SimpleNamespace(ins=stub, dutil=SimpleNamespace(mount=lambda name: esp))
            adapter.osins = SimpleNamespace(efi_part=SimpleNamespace(uuid=ESP, name="disk-test"),
                                           firmware_package=SimpleNamespace(path=package))
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
                             restore / "BuildManifest.plist", esp / "vendorfw/firmware.cpio"):
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed")
                    with self.subTest(path=path.name), self.assertRaises((ValueError, plistlib.InvalidFileException)):
                        adapter.boot_input_evidence()
                    path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
