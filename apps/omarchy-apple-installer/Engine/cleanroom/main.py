# SPDX-License-Identifier: MIT
"""Closed cleanroom entry point; all writes use the shared approved plan."""

import omarchy_execution
import omarchy_planner

import logging
import os
from pathlib import Path
from types import SimpleNamespace

from asahi_main import InstallerMain
from adapter import CleanroomStage1Adapter
from boot_inputs import BootInputError, load_profile, validate_host
from boot_space import STUB_SIZE
from omarchy_runtime import EngineRuntime, EngineRuntimeError


class CleanroomRuntime(EngineRuntime):
    developer_model_override = None

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        runtime = super().from_environment(environment)
        override = environment.get("OMARCHY_DEVELOPER_MODEL_OVERRIDE")
        if override is not None and override != "apple,j713":
            raise EngineRuntimeError("unknown developer model override")
        if runtime is None:
            if override is not None:
                raise EngineRuntimeError("developer override requires an engine mode")
            return None
        if runtime.mode != "inspect":
            if runtime.mode == "plan":
                identity = omarchy_planner._load_exact_json(
                    runtime.values["OMARCHY_ENGINE_IDENTITY"],
                    omarchy_planner.ENGINE_IDENTITY_KEYS, "planning identity")
            else:
                identity = omarchy_execution._load_exact_json(
                    runtime.values["OMARCHY_ENGINE_IDENTITY"],
                    omarchy_execution.IDENTITY_KEYS, "identity")
            if identity.get("developer_model_override") != override:
                raise EngineRuntimeError("developer override differs from authenticated identity")
        runtime.developer_model_override = override
        return runtime

    def inspect(self, device_class, supported):
        selected = self.developer_model_override
        super().inspect(selected.removeprefix("apple,") if selected else device_class, supported)

    def run_layout(self, *, installer, free_parts, resizable_parts, stub_size, part_align):
        # One value must reach both the planner and the partition writer. Keep
        # the upstream enumeration threshold so older stubs stay recognizable.
        return super().run_layout(installer=installer, free_parts=free_parts,
                                  resizable_parts=resizable_parts, stub_size=STUB_SIZE,
                                  part_align=part_align)

    def __init__(self, **kwargs):
        super().__init__(stage1_adapter_factory=CleanroomStage1Adapter, **kwargs)
        if self.values.get("OMARCHY_ENGINE_REPAIR_MANIFEST"):
            raise EngineRuntimeError("cleanroom V1 supports fresh installation only")


class CleanroomInstaller(InstallerMain):
    def __init__(self, version, *, engine_runtime):
        if engine_runtime is None:
            raise EngineRuntimeError("cleanroom requires an authenticated engine mode")
        self.cleanroom_profile = load_profile("cleanroom/profiles/j713.json")
        super().__init__(version, engine_runtime=engine_runtime)

    def host_supported(self):
        host = self.sysinfo
        overridden = (getattr(self.engine_runtime, "developer_model_override", None)
                      == self.cleanroom_profile["device_identifier"])
        if not overridden:
            try:
                validate_host(self.cleanroom_profile, product_type=host.product_type,
                              device_class=host.device_class, board_id=host.board_id,
                              chip_id=host.chip_id)
            except BootInputError as error:
                logging.warning("Host rejected: %s", error)
                return False
        # Normal admission requires the qualified host baseline. An explicit
        # developer profile override also admits a different host macOS version;
        # a compatible Apple boot build is selected from authenticated inputs
        # during preflight, independently of the Linux firmware baseline.
        # Recovery execution is exclusively the separately reviewed step2.
        # bputil requires root on current macOS. A non-privileged inventory
        # may report its model capability without pretending to know boot
        # policy; the root install path must positively identify macOS again.
        readonly_unknown = (host.boot_mode == "Unknown" and os.geteuid() != 0
                            and self.engine_runtime.mode in ("inspect", "plan"))
        if host.boot_mode != "macOS" and not readonly_unknown:
            logging.warning("Host rejected: boot mode %s is not installed macOS", host.boot_mode)
            return False
        baseline = self.cleanroom_profile["firmware"]["version"]
        if overridden:
            logging.info("Developer override: profile %s, host %s on macOS %s; "
                         "Linux firmware baseline %s; Apple boot build will be selected using SFR",
                         self.cleanroom_profile["device_identifier"], host.product_type,
                         host.macos_ver, baseline)
            return True
        if host.macos_ver != baseline:
            logging.warning("Host rejected: macOS %s differs from qualified baseline %s; "
                            "developer override is disabled", host.macos_ver, baseline)
            return False
        return True

    def choose_ipsw(self, supported_fw=None):
        firmware = self.cleanroom_profile["firmware"]
        if supported_fw != [firmware["version"]]:
            raise BootInputError("OS metadata has a different firmware baseline")
        restore = getattr(self, "cleanroom_restore_path", None)
        if not isinstance(restore, Path) or not restore.is_file():
            raise BootInputError("Apple restore input has not passed preflight")
        boot = getattr(self, "cleanroom_boot_profile", None)
        if not isinstance(boot, dict):
            raise BootInputError("Apple boot version has not passed preflight")
        return SimpleNamespace(version=boot["firmware"]["version"], url=str(restore))


def main():
    logging.basicConfig(filename="installer.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    runtime = CleanroomRuntime.from_environment()
    CleanroomInstaller(Path("version.tag").read_text().strip(),
                       engine_runtime=runtime).main()


if __name__ == "__main__":
    main()
