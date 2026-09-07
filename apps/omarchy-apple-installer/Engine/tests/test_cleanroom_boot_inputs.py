# SPDX-License-Identifier: MIT
import copy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import sys
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanroom"))
from boot_inputs import BootInputError, assemble_stage1, inspect_ipsw, load_profile, validate_host, stub_members, apple_boot_identity, select_apple_boot_profile, maximum_apple_selection_bytes
from recovery import prepare_recovery


PROFILE = Path(__file__).resolve().parents[1] / "cleanroom/profiles/j713.json"
ESP = "11111111-2222-4333-8444-555555555555"


class CleanroomBootInputsTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILE)
        self.base = "test.dmg.aea"
        self.components = {
            "BaseSystem": self.base,
            "BaseSystemVolume": "Firmware/" + self.base + ".root_hash",
            "Ap,BaseSystemTrustCache": "Firmware/" + self.base + ".trustcache",
            "Multitouch": "Firmware/J713_Multitouch.im4p",
        }
        self.identity = {
            "ApBoardID": "0x2C", "ApChipID": "0x8132",
            "Info": {"DeviceClass": "j713ap", "BuildNumber": "25G83",
                     "Variant": "macOS Customer", "RestoreBehavior": "Erase"},
            "Manifest": {key: {"Info": {"Path": value}}
                         for key, value in self.components.items()},
        }
        self.manifest = {
            "ProductVersion": "26.6.2", "ProductBuildVersion": "25G83",
            "SupportedProductTypes": ["Mac16,12"],
            "BuildIdentities": [self.identity],
        }

    def archive(self, duplicate=None, missing=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("BuildManifest.plist", plistlib.dumps(self.manifest))
            archive.writestr("SystemVersion.plist", plistlib.dumps({
                "ProductVersion": "26.6.2", "ProductBuildVersion": "25G83",
            }))
            for path in self.components.values():
                if path != missing:
                    archive.writestr(path, b"AEA1example" if path == self.base else b"opaque")
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(duplicate, b"duplicate")
        buffer.seek(0)
        return zipfile.ZipFile(buffer)

    def test_apple_boot_identity_follows_real_hardware_without_a_model_allowlist(self):
        host = SimpleNamespace(product_type="Mac99,1", device_class="j999ap", board_id=100, chip_id=0x8140)
        lock = {"supported_products": ["Mac99,1"], "members": {"boot": {}},
                "boot_identities": [{"device_class": "j999ap", "board_id": 100, "chip_id": 0x8140,
                                     "members": ["boot"]}]}
        selected = select_apple_boot_profile(self.profile, lock, host)
        self.assertEqual(selected["device_identifier"], "apple,j999")
        self.assertEqual(self.profile["device_identifier"], "apple,j713")
        self.assertEqual(selected["firmware"], self.profile["firmware"])
        for key, wrong in (("product_type", "Mac16,12"), ("device_class", "j713ap"),
                           ("board_id", 44), ("chip_id", 0x8132)):
            with self.subTest(key=key), self.assertRaisesRegex(BootInputError, "actual Mac"):
                select_apple_boot_profile(self.profile, lock, SimpleNamespace(**(vars(host) | {key: wrong})))

    def test_boot_identity_registry_rejects_duplicates_and_unpinned_members(self):
        identity = {"device_class": "j999ap", "board_id": 100, "chip_id": 0x8140, "members": ["boot"]}
        lock = {"supported_products": ["Mac99,1"], "members": {"boot": {}}, "boot_identities": [identity]}
        for identities in ([], [identity, identity], [dict(identity, members=["missing"])],
                           [dict(identity, board_id=True)], [dict(identity, members=[{}])]):
            with self.subTest(identities=identities), self.assertRaises(BootInputError):
                apple_boot_identity({**lock, "boot_identities": identities}, None)

    def test_cache_budget_reserves_native_fallback_or_largest_yolo_host(self):
        native = {"device_class": "j713ap", "board_id": 44, "chip_id": 0x8132, "members": ["common", "native"]}
        lock = {"supported_products": [self.profile["product_type"]],
                "system_image": {"member": "system"},
                "members": {name: {"size_bytes": size} for name, size in
                            (("common", 1), ("native", 2), ("system", 100), ("other", 20), ("third", 10))},
                "boot_identities": [native, dict(native, device_class="j999ap", members=["common", "other"]),
                                    dict(native, device_class="j888ap", members=["common", "third"])]}
        self.assertEqual(maximum_apple_selection_bytes(lock, self.profile), 103)

    def test_two_apple_identities_select_distinct_recovery_inputs(self):
        neo = {**self.profile, "device_identifier": "apple,j700", "product_type": "Mac17,5",
               "device_class": "j700ap", "board_id": 100, "chip_id": 0x8140}
        identity = copy.deepcopy(self.identity)
        identity.update(ApBoardID="0x64", ApChipID="0x8140")
        identity["Info"]["DeviceClass"] = "j700ap"
        for component, value in identity["Manifest"].items():
            old = value["Info"]["Path"]
            value["Info"]["Path"] = old.replace("test.dmg", "neo.dmg").replace("J713", "J700")
            self.components["Neo" + component] = value["Info"]["Path"]
        self.manifest["BuildIdentities"].append(identity)
        self.manifest["SupportedProductTypes"].append("Mac17,5")
        with self.archive() as archive:
            # The fixture writes the generic AEA marker only for self.base.
            with self.assertRaisesRegex(BootInputError, "BaseSystem format"):
                inspect_ipsw(archive, neo)
        self.base = "neo.dmg.aea"
        with self.archive() as archive:
            selection = inspect_ipsw(archive, neo)
            self.assertEqual(selection["manifest"]["BuildIdentities"], [identity])
            self.assertEqual(selection["recovery"]["image"], "neo.dmg.aea")
            self.assertEqual(selection["recovery"]["root_hash"], "Firmware/neo.dmg.aea.root_hash")

    def test_profile_contains_model_inputs_and_no_installation_identity(self):
        self.assertEqual(self.profile["chip_id"], 0x8132)
        validate_host(self.profile, product_type="Mac16,12", device_class="j713ap",
                      board_id=0x2C, chip_id=0x8132)
        for field, wrong in (("product_type", "Mac16,1"), ("device_class", "j614sap"),
                             ("board_id", 0x2D), ("chip_id", 0x8130)):
            values = dict(product_type="Mac16,12", device_class="j713ap",
                          board_id=0x2C, chip_id=0x8132)
            values[field] = wrong
            with self.subTest(field=field), self.assertRaises(BootInputError):
                validate_host(self.profile, **values)

    def test_profile_rejects_duplicate_fields_and_unpinned_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaises(BootInputError):
                load_profile(path)
            value = copy.deepcopy(self.profile)
            value["sources"]["linux"] = "main"
            path.write_text(json.dumps(value))
            with self.assertRaises(BootInputError):
                load_profile(path)

    def test_exact_identity_retains_encrypted_format_and_authentication_set(self):
        with self.archive() as archive:
            result = inspect_ipsw(archive, self.profile)
        self.assertEqual(result["recovery"]["format"], "aea")
        self.assertEqual(result["recovery"]["image"], self.base)
        self.assertEqual(result["recovery"]["root_hash"], self.components["BaseSystemVolume"])
        self.assertEqual(result["manifest"]["BuildIdentities"], [self.identity])

    def test_no_identity_and_ambiguous_identity_refuse(self):
        for identities in ([], [self.identity, self.identity]):
            self.manifest["BuildIdentities"] = identities
            with self.subTest(identities=len(identities)), self.archive() as archive:
                with self.assertRaises(BootInputError):
                    inspect_ipsw(archive, self.profile)

    def test_wrong_build_and_mixed_recovery_refuse(self):
        self.identity["Info"]["BuildNumber"] = "25G84"
        with self.archive() as archive, self.assertRaises(BootInputError):
            inspect_ipsw(archive, self.profile)
        self.identity["Info"]["BuildNumber"] = "25G83"
        self.identity["Manifest"]["BaseSystemVolume"]["Info"]["Path"] = "Firmware/other.root_hash"
        with self.archive() as archive, self.assertRaises(BootInputError):
            inspect_ipsw(archive, self.profile)

    def test_duplicate_missing_and_unsafe_firmware_refuse(self):
        path = self.components["Multitouch"]
        for options in ({"duplicate":path}, {"missing":path}):
            with self.subTest(options=options), self.archive(**options) as archive:
                with self.assertRaises(BootInputError):
                    inspect_ipsw(archive, self.profile)
        self.identity["Manifest"]["Multitouch"]["Info"]["Path"] = "Firmware/../../outside"
        with self.archive() as archive, self.assertRaises(BootInputError):
            inspect_ipsw(archive, self.profile)

    def full_stub_archive(self, *, missing=None, link_target=None):
        buffer = io.BytesIO()
        with self.archive() as original, zipfile.ZipFile(buffer, "w") as archive:
            for item in original.infolist():
                archive.writestr(item, original.read(item))
            framework = "BootabilityBundle/Restore/Bootability/BootabilityBrain.framework/"
            files = {
                "PlatformSupport.plist", "RestoreVersion.plist", "usr/standalone/bootcaches.plist",
                "BootabilityBundle/Restore/Firmware/Bootability.dmg.trustcache",
                "BootabilityBundle/Restore/Bootability/System/Library/CoreServices/RestoreVersion.plist",
                "Firmware/Manifests/restore/macOS Customer/apticket.j713ap.im4m",
                framework + "Versions/A/BootabilityBrain",
                framework + "Versions/A/Resources/Info.plist",
                framework + "Versions/A/_CodeSignature/CodeResources",
            }
            for name in sorted(files):
                if name != missing:
                    archive.writestr(name, b"opaque")
            for name in ("Versions/A/", "Versions/A/Resources/"):
                archive.writestr(framework + name, b"")
            for name, target in (("BootabilityBrain", "Versions/Current/BootabilityBrain"),
                                 ("Resources", "Versions/Current/Resources"),
                                 ("Versions/Current", link_target or "A")):
                item = zipfile.ZipInfo(framework + name)
                item.create_system = 3
                item.external_attr = 0o120755 << 16
                archive.writestr(item, target)
        return zipfile.ZipFile(buffer)

    def test_complete_stub_input_closure_is_required_before_allocation(self):
        with self.full_stub_archive() as archive:
            members = stub_members(archive, self.profile)
            self.assertIn("PlatformSupport.plist", members)
        for missing in ("PlatformSupport.plist", "RestoreVersion.plist",
                        "BootabilityBundle/Restore/Firmware/Bootability.dmg.trustcache"):
            with self.subTest(missing=missing), self.full_stub_archive(missing=missing) as archive:
                with self.assertRaisesRegex(BootInputError, "missing or duplicate"):
                    stub_members(archive, self.profile)

    def test_restore_symlinks_cannot_escape_or_cycle(self):
        for target in ("../../../outside", "Current", "/etc"):
            with self.subTest(target=target), self.full_stub_archive(link_target=target) as archive:
                with self.assertRaises(BootInputError):
                    stub_members(archive, self.profile)

    def test_stage1_binds_new_esp_and_aligns_complete_object(self):
        for length in (4096, 16300, 16384):
            base = b"\x01" * length
            with self.subTest(length=length):
                result = assemble_stage1(base, expected_sha256=hashlib.sha256(base).hexdigest(),
                                         esp_uuid=ESP)
                expected = (f"chosen.asahi,efi-system-partition={ESP}\n"
                            f"chainload={ESP};m1n1/boot.bin\n").encode()
                self.assertEqual(result[:length], base)
                self.assertEqual(result[length:length + len(expected)], expected)
                self.assertEqual(result[len(base) + len(expected):],
                                 b"\0" * (len(result) - length - len(expected)))
                self.assertGreaterEqual(len(result) - length - len(expected), 4)
                self.assertEqual(len(result) % 16384, 0)

    def test_stage1_rejects_stale_digest_stage2_and_rebinding(self):
        base = b"\x01" * 4096
        for suffix, digest in ((b"", "0" * 64),
                               (b"Chainloading files not supported in this build!", None),
                               (b"chainload=" + ESP.encode(), None)):
            candidate = base + suffix
            with self.subTest(suffix=suffix), self.assertRaises(BootInputError):
                assemble_stage1(candidate,
                                expected_sha256=digest or hashlib.sha256(candidate).hexdigest(),
                                esp_uuid=ESP)
        for invalid in ("disk0s3", "00000000-0000-0000-0000-000000000000", ESP + "\nother=1"):
            with self.subTest(uuid=invalid), self.assertRaises(BootInputError):
                assemble_stage1(base, expected_sha256=hashlib.sha256(base).hexdigest(), esp_uuid=invalid)

    def test_stage1_allows_compiled_parser_keyword(self):
        base = b"\x01" * 4096 + b"chainload=\0"
        result = assemble_stage1(base, expected_sha256=hashlib.sha256(base).hexdigest(), esp_uuid=ESP)
        self.assertTrue(result.startswith(base))

    def test_recovery_is_verified_before_output_publication(self):
        calls = []
        def run(command, *, check):
            self.assertTrue(check)
            calls.append(command)
            if command[0] == "/engine/restore-image":
                self.assertEqual(Path(command[1]).read_bytes(), b"AEA1example")
                self.assertEqual(command[2], hashlib.sha256(b"AEA1example").hexdigest())
                Path(command[3]).write_bytes(b"decoded disk image")
            else:
                self.assertEqual(command[:3], ["/usr/bin/hdiutil", "verify", "-quiet"])
        with tempfile.TemporaryDirectory() as directory, self.archive() as archive:
            target = Path(directory) / "Recovery.dmg"
            receipt = prepare_recovery(archive, self.profile, target, "/engine/restore-image", run=run)
            self.assertEqual(target.read_bytes(), b"decoded disk image")
            self.assertEqual(receipt["decoded"]["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(receipt["authentication"]["root_hash"]["member"], self.components["BaseSystemVolume"])
            self.assertEqual(len(calls), 2)
            with self.assertRaises(BootInputError):
                prepare_recovery(archive, self.profile, target, "/engine/restore-image", run=run)

    def test_failed_recovery_verification_leaves_no_publishable_image(self):
        def run(command, *, check):
            if command[0] == "/engine/restore-image":
                Path(command[3]).write_bytes(b"damaged image")
            else:
                raise subprocess.CalledProcessError(1, command)
        with tempfile.TemporaryDirectory() as directory, self.archive() as archive:
            target = Path(directory) / "Recovery.dmg"
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_recovery(archive, self.profile, target, "/engine/restore-image", run=run)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
