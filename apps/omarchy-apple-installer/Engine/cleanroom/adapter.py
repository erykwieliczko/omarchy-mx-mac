# SPDX-License-Identifier: MIT
"""Cleanroom boot content using the shared candidate-bound disk transaction."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import zipfile

from asahi_firmware.multitouch import MultitouchFWCollection
from apple_inputs import AppleWorkspace, load_apple_inputs, mounted_system_image, progress, retain_stub_inputs, verify_retained_workspace
from apple_ranges import selected_archive
from firmware import collect_macos_wifi
from firmware_ranges import extract_wifi, FirmwareRangeFallback
import osinstall
import stub

from boot_inputs import BootInputError, assemble_stage1, inspect_ipsw, validate_host
from recovery import prepare_recovery
from omarchy_asahi import AsahiStage1Adapter, load_metadata
from boot_inputs import _member, _path, stub_members


FIRMWARE_NAMES = {
    "apple/tpmtfw-j713.bin",
    "brcm/brcmfmac4388c2-pcie.apple,garden.bin",
    "brcm/brcmfmac4388c2-pcie.apple,garden.sig",
    "brcm/brcmfmac4388c2-pcie.apple,garden.clm_blob",
    "brcm/brcmfmac4388c2-pcie.apple,garden.txcap_blob",
    "brcm/brcmfmac4388c2-pcie.apple,garden-WLMT-u.txt",
    "brcm/brcmfmac4388c2-pcie.apple,garden-WLMT-a.txt",
}


def restore_layout(archive, profile):
    info = _member(archive, "usr/standalone/bootcaches.plist", 1024 * 1024)
    bootcaches = plistlib.loads(archive.read(info))
    bless = bootcaches.get("bless2", {})
    if (profile["firmware"]["build"] != "25G83"
            or bless.get("Version") != 1
            or bless.get("SupportsPairedRecovery") is not True
            or "RestoreBundlePath" in bless):
        raise BootInputError("unrecognized Apple restore layout")
    bless["RestoreBundlePath"] = "restore"
    return bootcaches


def file_descriptor(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as reader:
        while chunk := reader.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def cleanroom_spec(metadata, profile):
    templates = metadata.get("os_list", [])
    if (not isinstance(templates, list) or len(templates) != 1
            or not isinstance(templates[0], dict)):
        raise BootInputError("cleanroom metadata must have exactly one OS")
    spec = templates[0].get("cleanroom")
    if not isinstance(spec, dict) or set(spec) != {
        "schema_version", "device_identifier", "firmware_build", "sources",
        "apple_inputs", "stage1", "linux_firmware",
    }:
        raise BootInputError("invalid cleanroom metadata")
    if (type(spec["schema_version"]) is not int or spec["schema_version"] != 2
            or spec["device_identifier"] != profile["device_identifier"]
            or spec["firmware_build"] != profile["firmware"]["build"]
            or spec["sources"] != profile["sources"]):
        raise BootInputError("cleanroom metadata differs from engine profile")
    expected_apple = load_apple_inputs(
        Path(__file__).parent / "cleanroom/profiles/j713-apple-inputs.json", profile)
    if (spec["apple_inputs"] != expected_apple
            or spec["linux_firmware"] != expected_apple["linux_firmware"]):
        raise BootInputError("Apple inputs differ from the bundled source lock")
    for role in ("stage1",):
        descriptor = spec[role]
        if (not isinstance(descriptor, dict)
                or set(descriptor) != {"size_bytes", "sha256"}
                or type(descriptor["size_bytes"]) is not int
                or descriptor["size_bytes"] <= 0
                or not isinstance(descriptor["sha256"], str)
                or len(descriptor["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in descriptor["sha256"])):
            raise BootInputError("invalid cleanroom " + role + " descriptor")
    firmware = spec["linux_firmware"]
    if not isinstance(firmware, dict) or set(firmware) != FIRMWARE_NAMES:
        raise BootInputError("cleanroom Linux firmware inventory is required")
    for name, digest in firmware.items():
        _path(name)
        if (not isinstance(name, str) or not name.startswith(("apple/", "brcm/"))
                or ".." in Path(name).parts or "\\" in name
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise BootInputError("invalid cleanroom Linux firmware descriptor")
    return spec


class CleanroomStubInstaller(stub.StubInstaller):
    def __init__(self, *args, profile, selection, decoded, firmware, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.selection = selection
        self.decoded = Path(decoded)
        self.cleanroom_firmware = firmware

    def load_ipsw(self, ipsw_info):
        # This admitted subset is an IPSW layout regardless of its ZIP suffix.
        self.install_version = self.profile["firmware"]["version"]
        self.pkg = zipfile.ZipFile(ipsw_info.url)
        self.is_ota = False
        restore_layout(self.pkg, self.profile)

    def load_identity(self):
        self.get_paths()
        # This versioned compatibility adapter supplies the restore-directory
        # location removed from the 26.x bootcaches metadata. The Apple plist
        # copied to disk remains byte-identical to the selected IPSW member.
        self.bootcaches = restore_layout(self.pkg, self.profile)
        self.manifest = self.selection["manifest"]
        self.all_identities = self.manifest["BuildIdentities"]
        self.identity = self.all_identities[0]
        self.variant = self.profile["firmware"]["variant"]
        self.behavior = self.profile["firmware"]["restore_behavior"]
        return self.identity

    def copy_compress(self, source, destination):
        if source != self.selection["recovery"]["image"]:
            raise BootInputError("unexpected Recovery image copy")
        shutil.copyfile(self.decoded, destination)
        if file_descriptor(destination) != file_descriptor(self.decoded):
            raise BootInputError("installed Recovery image differs from prepared image")

    def collect_firmware(self, package):
        # Conversion was completed and checked before disk mutation. Do not run
        # unrelated camera/kernel firmware extractors for this boot stack.
        package.add_files(self.cleanroom_firmware)


class CleanroomStage1Adapter(AsahiStage1Adapter):
    def __init__(self, *, installer, **kwargs):
        super().__init__(installer=installer, stub_factory=self._make_stub,
                         os_factory=self._make_os, **kwargs)
        self.profile = installer.cleanroom_profile
        self.spec = None
        self.workspace = None

    @property
    def developer_override_enabled(self):
        runtime = getattr(self.installer, "engine_runtime", None)
        return getattr(runtime, "developer_model_override", None) == self.profile["device_identifier"]

    def preflight(self, plan):
        if self.preflight_complete:
            return
        if plan.candidate_kind not in ("free", "resize"):
            raise BootInputError("cleanroom V1 requires a fresh-install allocation")
        if plan.device_identifier != self.profile["device_identifier"]:
            raise BootInputError("plan model differs from cleanroom profile")
        host = self.installer.sysinfo
        if not self.developer_override_enabled:
            validate_host(self.profile, product_type=host.product_type,
                          device_class=host.device_class, board_id=host.board_id,
                          chip_id=host.chip_id)
        self.spec = cleanroom_spec(load_metadata(self.metadata_path), self.profile)
        self.stage1_path = Path("boot/m1n1.bin")
        self.verifier_path = Path("tools/omarchy-restore-image").resolve()
        if file_descriptor(self.stage1_path) != self.spec["stage1"]:
            raise BootInputError("engine stage-1 artifact differs from metadata")
        self.workspace = AppleWorkspace()
        work = Path(self.workspace.name)
        if shutil.disk_usage(work).free < self.spec["apple_inputs"]["preflight_scratch_bytes"]:
            raise BootInputError("Apple firmware preparation needs 64 GiB of temporary free space")
        # Our OS package contains no Apple archive. Fetch the signed metadata's
        # exact Apple build before Recovery preparation or partition allocation.
        with zipfile.ZipFile(self.payload_path) as payload:
            if any(item.filename == "apple-restore.zip" for item in payload.infolist()):
                raise BootInputError("firmware-free engine rejects bundled Apple restore inputs")
        cache = None
        if (Path(__file__).parent / "cleanroom/development-apple-cache").is_file():
            cache = Path("/var/db/com.omarchy.mx.installer-dev-cache")
        wifi = None
        try:
            wifi = extract_wifi(self.spec["apple_inputs"], self.profile,
                                Path(__file__).parent / "cleanroom/profiles",
                                self.verifier_path, work / "range-firmware")
        except FirmwareRangeFallback as error:
            progress("Firmware range download failed: " + str(error))
            progress("Falling back to the fully verified Apple system image; this downloads about 10.3 GB more")
        restore = selected_archive(self.spec["apple_inputs"], self.profile, work / "Apple.ipsw",
                                   cache_directory=cache, include_system=wifi is None)
        self.decoded = work / "BaseSystem.dmg"
        with zipfile.ZipFile(restore) as archive:
            stub_members(archive, self.profile)
            self.selection = inspect_ipsw(archive, self.profile)
            restore_layout(archive, self.profile)
            self.recovery_receipt = prepare_recovery(
                archive, self.profile, self.decoded, self.verifier_path)
            if wifi is None:
                with mounted_system_image(archive, self.spec["apple_inputs"], self.profile,
                                          work, self.verifier_path) as system_root:
                    wifi = collect_macos_wifi(self.profile, system_root)
            self.firmware = self._collect_linux_firmware(archive, work, wifi)
        restore = retain_stub_inputs(restore, self.profile, work / "apple-restore.zip")
        verify_retained_workspace(work, self.spec["apple_inputs"]["execution_scratch_bytes"])
        progress("Recovery, Wi-Fi and touchpad firmware verified; preparation complete")
        self.installer.cleanroom_restore_path = restore
        super().preflight(plan)

    def _collect_linux_firmware(self, archive, work, wifi):
        fud = work / "fud" / self.profile["device_identifier"].removeprefix("apple,")
        fud.mkdir(parents=True)
        path = self.selection["manifest"]["BuildIdentities"][0]["Manifest"]["Multitouch"]["Info"]["Path"]
        with archive.open(path) as reader, (fud / "Multitouch.im4p").open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        firmware = list(MultitouchFWCollection(str(fud.parent)).files())
        firmware.extend(wifi)
        selected = [(name, value) for name, value in firmware if name in self.spec["linux_firmware"]]
        observed = {name: hashlib.sha256(value.data).hexdigest() for name, value in selected}
        if len(observed) != len(selected) or observed != self.spec["linux_firmware"]:
            raise BootInputError("Linux payload firmware differs from admitted Apple inputs")
        return sorted(selected)

    def _make_stub(self, *args):
        return CleanroomStubInstaller(*args, profile=self.profile, selection=self.selection,
                                      decoded=self.decoded, firmware=self.firmware)

    def _make_os(self, *args):
        return osinstall.OSInstaller(*args, boot_object_builder=self._build_boot_object)

    def _build_boot_object(self, source, destination, variables):
        if Path(source).resolve() != self.stage1_path.resolve() or len(variables) != 2:
            raise BootInputError("unexpected cleanroom boot object request")
        prefix = "chosen.asahi,efi-system-partition="
        if not variables[0].startswith(prefix):
            raise BootInputError("missing generated ESP identity")
        identifier = variables[0][len(prefix):]
        if variables[1] != f"chainload={identifier};m1n1/boot.bin":
            raise BootInputError("inconsistent stage-1 chainload target")
        data = assemble_stage1(self.stage1_path.read_bytes(),
                               expected_sha256=self.spec["stage1"]["sha256"], esp_uuid=identifier)
        target = Path(destination)
        if target.resolve() != Path(self.installer.ins.boot_obj_path).resolve():
            raise BootInputError("stage-1 destination differs from prepared stub")
        target.write_bytes(data)
        os.chmod(target, 0o644)
        if target.read_bytes() != data:
            raise BootInputError("stage-1 read-back mismatch")
        self._write_recovery_script(identifier, data)

    def _write_recovery_script(self, esp_uuid, stage1):
        verifier = Path(self.installer.ins.step2_sh).with_name("omarchy-restore-image")
        shutil.copyfile(self.verifier_path, verifier)
        os.chmod(verifier, 0o755)
        script = self._recovery_script(esp_uuid, stage1)
        Path(self.installer.ins.step2_sh).write_text(script)
        os.chmod(self.installer.ins.step2_sh, 0o755)

    def _recovery_script(self, esp_uuid, stage1):
        script = Path("cleanroom/step2.sh").read_text()
        values = {
            "VGID": self.installer.ins.osi.vgid,
            "PRODUCT": (self.installer.sysinfo.product_type if self.developer_override_enabled
                        else self.profile["product_type"]),
            "ESP": esp_uuid.upper(),
            "STAGE1_SHA256": hashlib.sha256(stage1).hexdigest(),
            "STAGE1_SIZE": str(len(stage1)),
        }
        for key, value in values.items():
            if not isinstance(value, str) or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,-" for char in value):
                raise BootInputError("invalid Recovery script binding")
            script = script.replace("##" + key + "##", value)
        if "##" in script:
            raise BootInputError("unresolved Recovery script binding")
        return script

    def boot_input_evidence_keys(self):
        return {"cleanroom_boot_inputs"}

    def boot_input_evidence(self):
        installed = self.installer.ins
        installed.get_paths()
        expected_stage1 = assemble_stage1(
            self.stage1_path.read_bytes(), expected_sha256=self.spec["stage1"]["sha256"],
            esp_uuid=self.osins.efi_part.uuid)
        if Path(installed.boot_obj_path).read_bytes() != expected_stage1:
            raise BootInputError("installed raw stage 1 changed")
        if Path(installed.step2_sh).read_text() != self._recovery_script(
                self.osins.efi_part.uuid, expected_stage1):
            raise BootInputError("installed Recovery script changed")
        verifier = Path(installed.step2_sh).with_name("omarchy-restore-image")
        verifier_descriptor = file_descriptor(verifier)
        if verifier_descriptor != file_descriptor(self.verifier_path):
            raise BootInputError("installed Recovery verifier changed")
        recovery = (Path(installed.osi.recovery) / installed.osi.vgid
                    / "usr/standalone/firmware/arm64eBaseSystem.dmg")
        decoded = file_descriptor(recovery)
        if decoded != self.recovery_receipt["decoded"]:
            raise BootInputError("installed Recovery image changed")
        restore = Path(installed.pb_vgid) / "restore"
        authentication = {}
        for role, expected in self.recovery_receipt["authentication"].items():
            actual = file_descriptor(restore / expected["member"])
            if actual != {key: expected[key] for key in ("size_bytes", "sha256")}:
                raise BootInputError("installed Recovery authentication changed")
            authentication[role] = actual
        manifest = restore / "BuildManifest.plist"
        if manifest.read_bytes() != plistlib.dumps(self.selection["manifest"]):
            raise BootInputError("installed Apple build identity changed")
        esp = Path(self.installer.dutil.mount(self.osins.efi_part.name))
        vendor_firmware = {}
        for name in ("firmware.cpio", "firmware.tar", "manifest.txt"):
            actual = file_descriptor(esp / "vendorfw" / name)
            expected = file_descriptor(Path(self.osins.firmware_package.path) / name)
            if actual != expected:
                raise BootInputError("installed vendor firmware changed: " + name)
            vendor_firmware[name] = actual
        return {"cleanroom_boot_inputs": {
            "stage1": file_descriptor(installed.boot_obj_path),
            "step2": file_descriptor(installed.step2_sh),
            "verifier": verifier_descriptor,
            "recovery": decoded, "authentication": authentication,
            "manifest": file_descriptor(manifest),
            "vendor_firmware": vendor_firmware,
        }}
