"""Compatibility selection and immutable Apple boot-input admission."""
import copy
import io
import json
from pathlib import Path
import plistlib
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanroom"))
from boot_builds import load_boot_builds, restore_version, select_boot_build, verify_boot_version
from boot_inputs import BootInputError, load_profile

PROFILES = Path(__file__).resolve().parents[1] / "cleanroom/profiles"


class BootBuildTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILES / "j713.json")
        self.native = json.loads((PROFILES / "j713-apple-inputs.json").read_text())
        self.catalog, self.builds = load_boot_builds(PROFILES, self.native, self.profile)
        self.host = SimpleNamespace(product_type="Mac17,5", device_class="j700ap", board_id=100,
                                    chip_id=0x8140, sfr_full_ver="25.6.84.0.0,0")

    def test_actual_sfr_selects_compatible_boot_without_changing_linux(self):
        original = copy.deepcopy(self.profile)
        entry, lock, boot = select_boot_build(self.builds, self.profile, self.host)
        self.assertEqual(entry["firmware"]["build"], "25F84")
        self.assertEqual(boot["device_class"], "j700ap")
        self.assertEqual(boot["firmware"]["version"], "26.5.2")
        self.assertNotIn("system_image", lock)
        self.assertEqual(self.profile, original)
        self.host.sfr_full_ver = "25.7.83.0.0,0"
        self.assertEqual(select_boot_build(self.builds, self.profile, self.host)[0]["firmware"]["build"], "25G83")
        self.host.sfr_full_ver = "25.10.1.0.0,0"
        self.assertEqual(select_boot_build(self.builds, self.profile, self.host)[0]["firmware"]["build"], "25G83")

    def test_unknown_or_insufficient_sfr_is_never_overridden(self):
        for value in (None, "", "unknown", "25G83", "25.7.83", "25.7.83.0.0,0junk", "25.6.83.0.0,0"):
            self.host.sfr_full_ver = value
            with self.subTest(value=value), self.assertRaises(BootInputError):
                select_boot_build(self.builds, self.profile, self.host)

    def test_normal_mode_retains_qualified_apple_baseline(self):
        self.host = SimpleNamespace(**{key: self.profile[key] for key in
            ("product_type", "device_class", "board_id", "chip_id")}, sfr_full_ver="25.6.84.0.0,0")
        with self.assertRaisesRegex(BootInputError, "Update macOS to 26.6.2"):
            select_boot_build(self.builds, self.profile, self.host, allow_fallback=False)
        self.assertEqual(select_boot_build(self.builds, self.profile, self.host)[0]["firmware"]["build"], "25F84")

    def test_no_signed_hardware_identity_remains_fatal(self):
        self.host.device_class = "j999ap"
        with self.assertRaisesRegex(BootInputError, "actual Mac"):
            select_boot_build(self.builds, self.profile, self.host)

    def test_build_catalog_rejects_modified_lock_or_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for entry in self.catalog["builds"]:
                shutil.copyfile(PROFILES / entry["file"], root / entry["file"])
            catalog = copy.deepcopy(self.catalog)
            target = root / "apple-boot-builds.json"
            target.write_text(json.dumps(catalog))
            load_boot_builds(root, self.native, self.profile)
            file = root / catalog["builds"][-1]["file"]
            file.write_bytes(file.read_bytes() + b" ")
            with self.assertRaisesRegex(BootInputError, "authenticated catalog"):
                load_boot_builds(root, self.native, self.profile)
            catalog["builds"][0]["file"] = "../outside.json"
            target.write_text(json.dumps(catalog))
            with self.assertRaisesRegex(BootInputError, "reference"):
                load_boot_builds(root, self.native, self.profile)

    def test_selected_version_must_match_both_apple_plists(self):
        entry = select_boot_build(self.builds, self.profile, self.host)[0]
        brain = "BootabilityBundle/Restore/Bootability/System/Library/CoreServices/RestoreVersion.plist"
        for version, brain_version, accepted in (("25.6.84.0.0,0", "25.6.84.0.0,0", True),
                                                 ("25.7.83.0.0,0", "25.6.84.0.0,0", False),
                                                 ("25.6.84.0.0,0", "25.7.83.0.0,0", False)):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("RestoreVersion.plist", plistlib.dumps({"RestoreLongVersion": version}))
                archive.writestr(brain, plistlib.dumps({"RestoreLongVersion": brain_version}))
            with zipfile.ZipFile(output) as archive:
                if accepted:
                    verify_boot_version(archive, entry, self.host)
                else:
                    with self.assertRaisesRegex(BootInputError, "selected boot catalog"):
                        verify_boot_version(archive, entry, self.host)
