# SPDX-License-Identifier: MIT
"""Explicit Neo-only adapter for the development app's verified legacy engine.

Apple inputs/converters are reused from the owner's cleanroom implementation.
The signed OS image is unchanged; the separate Aurora bundle supplies the Neo kernel.
This development path does not establish physical Neo qualification.
"""
import copy
import hashlib
from pathlib import Path
import plistlib
import shutil
import zipfile

from apple_inputs import AppleWorkspace, load_apple_inputs, verify_retained_workspace
from apple_ranges import selected_archive
from boot_inputs import BootInputError, inspect_ipsw, load_profile
from boot_builds import restore_version, verify_boot_version
from boot_space import STUB_SIZE, check_prepared_space, check_installed_space
from firmware import convert_neo_wifi, convert_neo_touchpad, collect_neo_calibration
from firmware_archive import verify_package
from firmware_ranges import extract_wifi
from recovery import prepare_recovery
from aurora_boot import configure_aurora

ROOT = Path(__file__).resolve().parent
RESTORE_VERSION = "25.7.83.0.0,0"


class SystemFirmwareUpdateRequiredError(BootInputError):
    pass


def require_system_firmware(host):
    if restore_version(host.sfr_full_ver) < restore_version(RESTORE_VERSION):
        raise SystemFirmwareUpdateRequiredError("Update macOS to 26.6.2 or later")


def restore_layout(archive):
    value = plistlib.loads(archive.read("usr/standalone/bootcaches.plist"))
    bless = value.get("bless2", {})
    if (bless.get("Version") != 1 or bless.get("SupportsPairedRecovery") is not True
            or "RestoreBundlePath" in bless):
        raise BootInputError("unrecognized Neo 26.6.2 restore layout")
    bless["RestoreBundlePath"] = "restore"
    return value


def prepare_inputs(work, decoder, host=None):
    """Read-only Apple acquisition; complete before creating any partitions."""
    profile = load_profile(ROOT / "profiles/j700.json")
    lock = load_apple_inputs(ROOT / "profiles/j700-apple-inputs.json", profile)
    if shutil.disk_usage(work).free < 8 * 1024**3:
        raise BootInputError("Neo Apple preparation needs 8 GiB of temporary free space")
    wifi = extract_wifi(lock, profile, ROOT / "profiles", decoder, work / "range-firmware")
    firmware = convert_neo_wifi(wifi, lock, work / "neo-wifi")
    restore = selected_archive(lock, profile, work / "Apple.ipsw", include_system=False)
    decoded = work / "BaseSystem.dmg"
    with zipfile.ZipFile(restore) as archive:
        if host is not None:
            verify_boot_version(archive, {"restore_version": RESTORE_VERSION}, host)
        selection = inspect_ipsw(archive, profile)
        restore_layout(archive)
        receipt = prepare_recovery(archive, profile, decoded, decoder)
        check_prepared_space(archive, profile, receipt, STUB_SIZE)
        identity = selection["manifest"]["BuildIdentities"][0]
        touchpad = identity["Manifest"]["Multitouch"]["Info"]["Path"]
        firmware.extend(convert_neo_touchpad(archive.read(touchpad)))
    actual = {name: hashlib.sha256(value.data).hexdigest() for name, value in firmware}
    if len(actual) != len(firmware) or actual != lock["linux_firmware"]:
        raise BootInputError("Neo firmware inventory differs from pinned 26.6.2 inputs")
    verify_retained_workspace(work, 8 * 1024**3)
    return profile, restore, selection, decoded, firmware


def configure(request):
    if request.get("firmware_profile") != "apple,j700":
        raise BootInputError("Neo adapter requires an explicit Neo development selection")
    import stub
    import omarchy_runtime

    original_layout = omarchy_runtime.EngineRuntime.run_layout

    def run_layout(self, **kwargs):
        # Planning and execution must reserve the same larger APFS stub.
        kwargs["stub_size"] = STUB_SIZE
        return original_layout(self, **kwargs)

    omarchy_runtime.EngineRuntime.run_layout = run_layout
    original_stub = stub.StubInstaller

    class NeoStubInstaller(original_stub):
        def check_volume(self, part=None):
            super().check_volume(part)
            # Recovery retries start in a fresh process without load_identity().
            self.get_paths()

        def load_ipsw(self, ignored_legacy_ipsw):
            require_system_firmware(self.sysinfo)
            self.workspace = AppleWorkspace()
            work = Path(self.workspace.name)
            (self.profile, restore, self.selection, self.decoded,
             self.neo_firmware) = prepare_inputs(work, ROOT / "restore-image-tool", self.sysinfo)
            # Calibration belongs to the physical Neo, never a selected model.
            if request["physical_device"] == "apple,j700":
                self.neo_firmware.extend(collect_neo_calibration())
            self.sysinfo = copy.copy(self.sysinfo)
            for key in ("device_class", "product_type", "board_id", "chip_id"):
                setattr(self.sysinfo, key, self.profile[key])
            self.install_version = self.profile["firmware"]["version"]
            self.is_ota = False
            self.pkg = zipfile.ZipFile(restore)

        def load_identity(self):
            self.get_paths()
            self.bootcaches = restore_layout(self.pkg)
            self.manifest = copy.deepcopy(self.selection["manifest"])
            self.all_identities = self.manifest["BuildIdentities"]
            self.identity = self.all_identities[0]
            self.variant = self.profile["firmware"]["variant"]
            self.behavior = self.profile["firmware"]["restore_behavior"]
            return self.identity

        def copy_compress(self, source, destination):
            if source != self.selection["recovery"]["image"]:
                raise BootInputError("unexpected Neo Recovery image copy")
            shutil.copyfile(self.decoded, destination)
            def digest(path):
                with open(path, "rb") as reader:
                    value = hashlib.sha256()
                    while chunk := reader.read(1024 * 1024):
                        value.update(chunk)
                    return value.digest()
            if digest(self.decoded) != digest(destination):
                raise BootInputError("installed Neo Recovery differs from decoded input")

        def collect_firmware(self, package):
            package.add_files(self.neo_firmware)
            package.close()
            verify_package(package.path, self.neo_firmware)

        def prepare_for_bless(self):
            check_installed_space(self.osi.system)
            super().prepare_for_bless()

        def prepare_for_step2(self):
            check_installed_space(self.osi.system)
            super().prepare_for_step2()

    stub.StubInstaller = NeoStubInstaller
    configure_aurora(ROOT)
