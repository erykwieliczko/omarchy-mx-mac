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
        if getattr(self.engine_runtime, "developer_model_override", None) != self.cleanroom_profile["device_identifier"]:
            try:
                validate_host(self.cleanroom_profile, product_type=host.product_type,
                              device_class=host.device_class, board_id=host.board_id,
                              chip_id=host.chip_id)
            except BootInputError:
                return False
        # Stage one requires the same macOS baseline as its restore input.
        # Recovery execution is exclusively the separately reviewed step2.
        # bputil requires root on current macOS. A non-privileged inventory
        # may report its model capability without pretending to know boot
        # policy; the root install path must positively identify macOS again.
        readonly_unknown = (host.boot_mode == "Unknown" and os.geteuid() != 0
                            and self.engine_runtime.mode in ("inspect", "plan"))
        return ((host.boot_mode == "macOS" or readonly_unknown)
                and host.macos_ver == self.cleanroom_profile["firmware"]["version"])

    def choose_ipsw(self, supported_fw=None):
        firmware = self.cleanroom_profile["firmware"]
        if supported_fw != [firmware["version"]]:
            raise BootInputError("OS metadata has a different firmware baseline")
        restore = getattr(self, "cleanroom_restore_path", None)
        if not isinstance(restore, Path) or not restore.is_file():
            raise BootInputError("Apple restore input has not passed preflight")
        return SimpleNamespace(version=firmware["version"], url=str(restore))


def main():
    logging.basicConfig(filename="installer.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    runtime = CleanroomRuntime.from_environment()
    CleanroomInstaller(Path("version.tag").read_text().strip(),
                       engine_runtime=runtime).main()


if __name__ == "__main__":
    main()
