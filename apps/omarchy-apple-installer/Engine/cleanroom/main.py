# SPDX-License-Identifier: MIT
"""Closed cleanroom entry point; all writes use the shared approved plan."""

import logging
import os
from pathlib import Path
from types import SimpleNamespace

from asahi_main import InstallerMain
from adapter import CleanroomStage1Adapter
from boot_inputs import BootInputError, load_profile, validate_host
from omarchy_runtime import EngineRuntime, EngineRuntimeError


class CleanroomRuntime(EngineRuntime):
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
