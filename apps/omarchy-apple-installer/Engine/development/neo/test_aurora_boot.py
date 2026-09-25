# SPDX-License-Identifier: MIT
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import types
import sys
import unittest
from unittest.mock import patch
import zipfile

import aurora_boot as aurora


class AuroraBootTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def archive(self, entries):
        path = self.root / "bundle.tar.zst"
        with tarfile.open(path, "w") as writer:
            for info, content in entries:
                if isinstance(info, str):
                    info = tarfile.TarInfo(info)
                info.size = len(content)
                writer.addfile(info, io.BytesIO(content))
        return path

    def bundle(self):
        files = {"boot/m1n1-stage1.bin": b"stage1", "boot/boot.bin": b"stage2"}
        manifest = {
            "schema_version": 1, "kind": "aurora-silicon-boot", "devices": ["apple,j700"],
            "stage1": "boot/m1n1-stage1.bin", "stage2": "boot/boot.bin",
            "efi_path": aurora.EFI_PATH,
            "engine_sha256": "a" * 64, "os_sha256": "b" * 64,
            "files": {name: {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in files.items()},
        }
        data = aurora.canonical(manifest)
        path = self.archive([("manifest.json", data), *files.items()])
        pin = {"schema_version": 1, "size_bytes": path.stat().st_size,
               "url": "https://f005.backblazeb2.com/file/omarchymacexperimental/aurora/boot/test.tar.zst",
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "manifest_sha256": hashlib.sha256(data).hexdigest()}
        return path, pin, manifest, files

    def test_verified_download_and_corruption_rejection(self):
        path, pin, manifest, files = self.bundle()
        class Response(io.BytesIO):
            status = 200
            def geturl(self): return pin["url"]
        opener = types.SimpleNamespace(open=lambda *args, **kwargs: Response(path.read_bytes()))
        self.assertEqual(aurora.acquire_bundle(self.root, pin, opener=opener), (manifest, files))
        path.write_bytes(path.read_bytes()[:-1] + b"X")
        with self.assertRaisesRegex(aurora.BootInputError, "verification"):
            aurora.verify_bundle(path, pin)

    def test_archive_rejects_paths_links_duplicates_and_oversized_members(self):
        for name in ("../outside", "/outside", "boot/../outside", "boot\\outside"):
            with self.subTest(name=name), self.assertRaises(aurora.BootInputError):
                aurora.read_archive(self.archive([(name, b"data")]))
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            info = tarfile.TarInfo("boot/link")
            info.type, info.linkname = kind, "/etc/passwd"
            with self.assertRaises(aurora.BootInputError):
                aurora.read_archive(self.archive([(info, b"")]))
        with self.assertRaises(aurora.BootInputError):
            aurora.read_archive(self.archive([("duplicate", b"1"), ("duplicate", b"2")]))
        with patch.object(aurora, "MAX_CONTENTS", 1), self.assertRaises(aurora.BootInputError):
            aurora.read_archive(self.archive([("large", b"12")]))

    def test_manifest_model_and_component_checks(self):
        path, pin, manifest, files = self.bundle()
        for modification in ("model", "hash"):
            changed = json.loads(json.dumps(manifest))
            if modification == "model": changed["devices"] = ["apple,j413"]
            else: changed["files"]["boot/boot.bin"]["sha256"] = "0" * 64
            data = aurora.canonical(changed)
            path = self.archive([("manifest.json", data), *files.items()])
            changed_pin = {**pin, "size_bytes": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "manifest_sha256": hashlib.sha256(data).hexdigest()}
            with self.assertRaises(aurora.BootInputError):
                aurora.verify_bundle(path, changed_pin)

    def test_separate_esp_files_preserve_upstream_and_bind_bundle_identity(self):
        _, pin, manifest, files = self.bundle()
        with zipfile.ZipFile(io.BytesIO(), "w") as original:
            original.writestr("esp/m1n1/boot.bin", b"upstream")
            wrapped = aurora.AuroraOSArchive(original, manifest, files, pin)
            self.assertEqual(wrapped.read("esp/m1n1/boot.bin"), b"upstream")
            info = wrapped.getinfo("esp/" + aurora.EFI_PATH)
            self.assertEqual(wrapped.open(info).read(), b"stage2")
            self.assertEqual(info.file_size, 6)
            receipt = "esp/aurora/boot/bundle.json"
            other = aurora.AuroraOSArchive(original, manifest, files, {**pin, "sha256": "c" * 64})
            self.assertNotEqual(wrapped.read(receipt), other.read(receipt))
            self.assertTrue(wrapped.getinfo("esp/aurora/boot/").is_dir())

    def test_kernel_entry_is_default_and_stock_files_remain_fallback(self):
        _, pin, manifest, files = self.bundle()
        manifest["kernel"] = {"release": aurora.KERNEL_RELEASE, "menu_name": aurora.KERNEL_NAME,
                              "uki": "kernel/kernel.efi", "root": "kernel/root.tar.zst"}
        files.update({"kernel/kernel.efi": b"MZnew-kernel", "kernel/root.tar.zst": b"payload"})
        menu = ("default_entry: 2\n/+Omarchy\n  //linux-aurora\n"
                "  protocol: efi\n  path: boot():/EFI/Linux/stock.efi\n"
                "  cmdline: root=UUID=original rw rootflags=subvol=@\n")
        with zipfile.ZipFile(io.BytesIO(), "w") as original:
            original.writestr("esp/limine.conf", menu)
            original.writestr("esp/EFI/Linux/stock.efi", b"stock-kernel")
            original.writestr("root.img", b"untouched-root")
            wrapped = aurora.AuroraOSArchive(original, manifest, files, pin)
            self.assertEqual(wrapped.namelist().count("esp/limine.conf"), 1)
            self.assertEqual(sum(i.filename == "esp/limine.conf" for i in wrapped.infolist()), 1)
            updated = wrapped.read("esp/limine.conf").decode()
            self.assertIn("default_entry: Omarchy/" + aurora.KERNEL_NAME, updated)
            self.assertIn("//linux-aurora\n", updated)
            self.assertIn("//" + aurora.KERNEL_NAME + "\n", updated)
            self.assertEqual(updated.count("cmdline: root=UUID=original rw rootflags=subvol=@"), 2)
            self.assertEqual(wrapped.read("root.img"), b"untouched-root")
            self.assertEqual(wrapped.read("esp/EFI/Linux/stock.efi"), b"stock-kernel")
            self.assertEqual(wrapped.read("esp/" + aurora.KERNEL_ROOT), b"payload")

    def test_stage1_selects_aurora_and_is_bound_to_the_installation(self):
        first = aurora.stage1_image(b"stage1", "4550c53b-3bc2-4d8e-aa9a-3a51bf0521e7")
        second = aurora.stage1_image(b"stage1", "4550c53b-3bc2-4d8e-aa9a-3a51bf0521e8")
        self.assertNotEqual(first, second)
        self.assertEqual(len(first) % 16384, 0)
        self.assertIn(b";aurora/boot/boot.bin\n\0\0\0\0", first)
        with self.assertRaises(aurora.BootInputError):
            aurora.stage1_image(first, "4550c53b-3bc2-4d8e-aa9a-3a51bf0521e8")

    def test_real_engine_copies_and_verifies_aurora_and_refuses_changed_retry_bundle(self):
        from omarchy_asahi import AsahiStage1Adapter, AsahiAdapterError
        from osinstall import OSInstaller
        _, pin, manifest, files = self.bundle()
        with zipfile.ZipFile(io.BytesIO(), "w") as original:
            original.writestr("esp/original", b"upstream")
            wrapped = aurora.AuroraOSArchive(original, manifest, files, pin)
            installer = object.__new__(OSInstaller)
            installer.pkg, installer.verbose = wrapped, False
            installer.path = lambda path: path
            installer.fdcopy = lambda reader, writer, size: writer.write(reader.read(size))
            target = self.root / "esp"
            target.mkdir()
            installer.extract_tree("esp", str(target))
            adapter = object.__new__(AsahiStage1Adapter)
            adapter.osins = installer
            adapter.installer = types.SimpleNamespace(dutil=types.SimpleNamespace(mount=lambda _: str(target)))
            partition = types.SimpleNamespace(name="disk0s4")
            first = adapter._verify_copied_tree("esp", partition)
            self.assertGreater(first[0], len(b"upstream"))
            self.assertEqual(adapter._verify_copied_tree("esp", partition), first)
            installer.pkg = aurora.AuroraOSArchive(original, manifest, files, {**pin, "sha256": "d" * 64})
            with self.assertRaises(AsahiAdapterError):
                adapter._verify_copied_tree("esp", partition)

            installer.pkg = wrapped
            (target / aurora.EFI_PATH).write_bytes(b"broken")
            with self.assertRaises(AsahiAdapterError):
                adapter._verify_copied_tree("esp", partition)

    def test_adapter_preflight_stage1_and_readback_contract(self):
        _, pin, manifest, files = self.bundle()
        (self.root / "aurora-bundle.json").write_text(json.dumps(pin))
        original = zipfile.ZipFile(io.BytesIO(), "w")
        self.addCleanup(original.close)
        original.writestr("esp/m1n1/boot.bin", b"original")
        template = {"next_object": "m1n1/boot.bin"}
        esp_uuid = "4550c53b-3bc2-4d8e-aa9a-3a51bf0521e7"
        destination = self.root / "boot.bin"
        class Adapter:
            preflight_complete = False
            def preflight(self, plan):
                self.preflight_complete = True
                self.osins = types.SimpleNamespace(pkg=original, template=template,
                                                   efi_part=types.SimpleNamespace(uuid=esp_uuid))
                self.installer = types.SimpleNamespace(ins=types.SimpleNamespace(boot_obj_path=destination))
            def _installed_evidence(self, plan): return "verified upstream files"
        engine = types.SimpleNamespace(AsahiStage1Adapter=Adapter)
        m1n1 = types.SimpleNamespace()
        plan = types.SimpleNamespace(engine_digest="sha256:" + manifest["engine_sha256"],
                                     payload_digest="sha256:" + manifest["os_sha256"])
        with (patch.dict(sys.modules, m1n1=m1n1, omarchy_asahi=engine),
              patch.object(aurora, "acquire_bundle", return_value=(manifest, files)) as acquire):
            aurora.configure_aurora(self.root)
            instance = engine.AsahiStage1Adapter()
            instance.preflight(plan)
            self.addCleanup(instance.aurora_workspace.cleanup)
            instance.preflight(plan)
            self.assertEqual(acquire.call_count, 1)
            self.assertEqual(template["next_object"], "m1n1/boot.bin")
            self.assertEqual(instance.osins.template["next_object"], aurora.EFI_PATH)
            variables = ["chosen.asahi,efi-system-partition=" + esp_uuid,
                         "chainload=" + esp_uuid + ";" + aurora.EFI_PATH]
            m1n1.build("boot/m1n1.bin", destination, variables)
            self.assertEqual(instance._installed_evidence(plan), "verified upstream files")
            destination.write_bytes(b"changed")
            with self.assertRaises(aurora.BootInputError):
                instance._installed_evidence(plan)
            with self.assertRaises(aurora.BootInputError):
                m1n1.build("boot/m1n1.bin", destination, variables[:1])
            plan.engine_digest = "sha256:" + "f" * 64
            mismatch = engine.AsahiStage1Adapter()
            with self.assertRaisesRegex(aurora.BootInputError, "does not match"):
                mismatch.preflight(plan)
            mismatch.aurora_workspace.cleanup()


if __name__ == "__main__":
    unittest.main()
