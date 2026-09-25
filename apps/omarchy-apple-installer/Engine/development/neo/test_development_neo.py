# SPDX-License-Identifier: MIT
import io
import plistlib
import sys
import types
import unittest
import zipfile
from unittest.mock import patch

import development_neo as neo


class NeoAdapterTests(unittest.TestCase):
    def test_fresh_recovery_retry_initializes_installed_paths(self):
        from stub import StubInstaller
        class Runtime:
            def run_layout(self, **kwargs): return kwargs
        stub = types.SimpleNamespace(StubInstaller=StubInstaller)
        runtime = types.SimpleNamespace(EngineRuntime=Runtime)
        with (patch.dict(sys.modules, stub=stub, omarchy_runtime=runtime),
              patch.object(neo, "configure_aurora")):
            neo.configure({"firmware_profile": "apple,j700", "physical_device": "apple,j700"})
            instance = object.__new__(stub.StubInstaller)
            instance.osinfo = types.SimpleNamespace(collect_part=lambda _: [types.SimpleNamespace(
                system="/Volumes/Omarchy", preboot="/Volumes/Preboot", vgid="volume-group")])
            instance.check_volume(types.SimpleNamespace(name="disk0s4"))
            self.assertEqual(instance.boot_obj_path,
                             "/Volumes/Omarchy/Finish Installation.app/Contents/Resources/boot.bin")
            self.assertEqual(instance.icon_path, "/Volumes/Omarchy/.VolumeIcon.icns")

    def test_pinned_profile_and_firmware_inventory(self):
        profile = neo.load_profile(neo.ROOT / "profiles/j700.json")
        lock = neo.load_apple_inputs(neo.ROOT / "profiles/j700-apple-inputs.json", profile)
        self.assertEqual(profile["firmware"]["version"], "26.6.2")
        self.assertEqual(profile["firmware"]["build"], "25G83")
        self.assertEqual((profile["device_class"], profile["board_id"], profile["chip_id"]),
                         ("j700ap", 100, 0x8140))
        self.assertEqual(len(lock["linux_firmware"]), 250)

    def test_m1_m2_cannot_enter_neo_adapter(self):
        for selected in ("apple,j313", "apple,j413", None):
            with self.assertRaisesRegex(neo.BootInputError, "explicit Neo"):
                neo.configure({"firmware_profile": selected})

    def test_neo_requires_system_update_before_apple_preparation(self):
        with self.assertRaisesRegex(neo.SystemFirmwareUpdateRequiredError, "Update macOS to 26.6.2 or later"):
            neo.require_system_firmware(types.SimpleNamespace(sfr_full_ver="25.6.84.0.0,0"))
        for version in (neo.RESTORE_VERSION, "26.1.428.0.0,0"):
            neo.require_system_firmware(types.SimpleNamespace(sfr_full_ver=version))

    def test_layout_is_synthetic_only_in_memory(self):
        raw = plistlib.dumps({"bless2": {"Version": 1, "SupportsPairedRecovery": True}})
        archive = types.SimpleNamespace(read=lambda _: raw)
        value = neo.restore_layout(archive)
        self.assertEqual(value["bless2"]["RestoreBundlePath"], "restore")
        self.assertNotIn("RestoreBundlePath", plistlib.loads(raw)["bless2"])
        with self.assertRaises(neo.BootInputError):
            neo.restore_layout(types.SimpleNamespace(read=lambda _: plistlib.dumps({})))

    def test_neo_calibration_list_is_added_only_for_physical_neo(self):
        class Runtime:
            def run_layout(self, **kwargs): return kwargs
        class Stub:
            pass
        for physical in ("apple,j700", "apple,j413"):
            runtime = types.SimpleNamespace(EngineRuntime=Runtime)
            stub = types.SimpleNamespace(StubInstaller=Stub)
            profile = neo.load_profile(neo.ROOT / "profiles/j700.json")
            firmware = [("generic", object())]
            calibration = [("wcal", object()), ("oca2", object())]
            with (patch.dict(sys.modules, stub=stub, omarchy_runtime=runtime),
                  patch.object(neo, "configure_aurora"),
                  patch.object(neo, "prepare_inputs", return_value=(profile, "archive", {}, "decoded", firmware)),
                  patch.object(neo.zipfile, "ZipFile"),
                  patch.object(neo, "collect_neo_calibration", return_value=calibration) as collect):
                neo.configure({"firmware_profile": "apple,j700", "physical_device": physical})
                instance = stub.StubInstaller()
                instance.sysinfo = types.SimpleNamespace(sfr_full_ver=neo.RESTORE_VERSION)
                instance.load_ipsw(None)
                self.assertEqual(len(instance.neo_firmware), 3 if physical == "apple,j700" else 1)
                self.assertEqual(instance.sysinfo.device_class, "j700ap")
                self.assertEqual(collect.call_count, 1 if physical == "apple,j700" else 0)
                instance.workspace.cleanup()

    def test_neo_planning_and_execution_share_larger_stub(self):
        class Runtime:
            def run_layout(self, **kwargs):
                return kwargs
        class Stub:
            pass
        stub = types.SimpleNamespace(StubInstaller=Stub)
        runtime = types.SimpleNamespace(EngineRuntime=Runtime)
        with (patch.dict(sys.modules, stub=stub, omarchy_runtime=runtime),
              patch.object(neo, "configure_aurora")):
            neo.configure({"firmware_profile": "apple,j700", "physical_device": "apple,j700"})
            self.assertEqual(Runtime().run_layout(stub_size=2_500_000_000)["stub_size"], 4 * 1024**3)

    def test_restore_requires_compatible_firmware_and_exact_apple_versions(self):
        names = ("RestoreVersion.plist",
                 "BootabilityBundle/Restore/Bootability/System/Library/CoreServices/RestoreVersion.plist")
        host = types.SimpleNamespace(sfr_full_ver="25.6.84.0.0,0")
        entry = {"restore_version": neo.RESTORE_VERSION}
        for mismatch in (None, *names):
            with self.subTest(mismatch=mismatch), zipfile.ZipFile(io.BytesIO(), "w") as archive:
                for name in names:
                    version = host.sfr_full_ver if name == mismatch else neo.RESTORE_VERSION
                    archive.writestr(name, plistlib.dumps({"RestoreLongVersion": version}))
                if mismatch is None:
                    with self.assertRaisesRegex(neo.BootInputError, "System firmware"):
                        neo.verify_boot_version(archive, entry, host)
                    neo.verify_boot_version(archive, entry, types.SimpleNamespace(sfr_full_ver=neo.RESTORE_VERSION))
                else:
                    with self.assertRaisesRegex(neo.BootInputError, "differs from selected"):
                        neo.verify_boot_version(archive, entry, types.SimpleNamespace(sfr_full_ver=neo.RESTORE_VERSION))


if __name__ == "__main__":
    unittest.main()
