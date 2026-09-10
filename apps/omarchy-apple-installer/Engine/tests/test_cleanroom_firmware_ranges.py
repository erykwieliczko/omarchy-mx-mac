"""Check signed recipe binding and native failure classification without Apple bytes."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cleanroom"))
from boot_inputs import BootInputError, load_profile
from firmware_ranges import FirmwareRangeFallback, extract_wifi, load_recipe

PROFILES = Path(__file__).resolve().parents[1] / "cleanroom/profiles"


class FirmwareRangeTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(PROFILES / "j713.json")
        self.lock = json.loads((PROFILES / "j713-apple-inputs.json").read_text())

    def test_neo_recipe_preserves_m4_and_binds_separate_originals(self):
        neo = load_profile(PROFILES / "j700.json")
        lock = json.loads((PROFILES / "j700-apple-inputs.json").read_text())
        self.assertEqual(neo["sources"], self.profile["sources"])
        self.assertEqual(load_recipe(lock, neo, PROFILES).name, "j700-25G83-ranges.json")
        self.assertEqual(len(lock["linux_firmware"]), 249)
        self.assertEqual(len(self.lock["linux_firmware"]), 7)
        self.assertNotIn("mediatek/mt7932/wcal.bin", lock["linux_firmware"])
        self.assertNotIn("mediatek/mt7932/oca2.bin", lock["linux_firmware"])
        changed = copy.deepcopy(lock)
        changed["wifi_source_hashes"][next(iter(neo["wifi"]["files"]))] = "0" * 64
        with self.assertRaises(BootInputError):
            load_recipe(changed, neo, PROFILES)
        with self.assertRaises(BootInputError):
            load_recipe(lock, self.profile, PROFILES)

    def test_recipe_binds_apple_build_model_and_final_firmware_hashes(self):
        path = load_recipe(self.lock, self.profile, PROFILES)
        self.assertEqual(path.name, "j713-25G83-ranges.json")
        for mutate in (
            lambda x: x["ipsw"].update(sha256="0" * 64),
            lambda x: x["system_image"].update(member="other.dmg.aea"),
            lambda x: x["firmware_ranges"].update(file_name="../recipe.json"),
            lambda x: x["firmware_ranges"].update(sha256="0" * 64),
            lambda x: x["linux_firmware"].update({next(iter(self.profile["wifi"]["files"])): "0" * 64}),
        ):
            changed = copy.deepcopy(self.lock)
            mutate(changed)
            with self.assertRaises(BootInputError):
                load_recipe(changed, self.profile, PROFILES)

    def test_malformed_recipe_and_file_inventory_rejected(self):
        recipe = json.loads(load_recipe(self.lock, self.profile, PROFILES).read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.lock["firmware_ranges"]["file_name"]
            changed = copy.deepcopy(recipe)
            changed["files"][0] = None
            for content in (b"[", b"[]", json.dumps(changed).encode()):
                path.write_bytes(content)
                self.lock["firmware_ranges"].update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
                with self.assertRaises(BootInputError):
                    load_recipe(self.lock, self.profile, directory)

    def test_only_remote_failure_requests_full_image_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            for code in (75, 1, -15, -2):
                run = Mock(side_effect=subprocess.CalledProcessError(code, ["decoder"]))
                with self.subTest(code=code), self.assertRaises(BootInputError) as raised:
                    extract_wifi(self.lock, self.profile, PROFILES, "/decoder", Path(directory) / "out", run=run)
                self.assertEqual(isinstance(raised.exception, FirmwareRangeFallback), code == 75)
                run.assert_called_once()

    def test_damaged_local_recipe_never_starts_native_decoder(self):
        self.lock["firmware_ranges"]["sha256"] = "0" * 64
        run = Mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(BootInputError):
            extract_wifi(self.lock, self.profile, PROFILES, "/decoder", Path(directory) / "out", run=run)
        run.assert_not_called()
