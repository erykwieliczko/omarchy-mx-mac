# SPDX-License-Identifier: MIT
"""Reserve APFS space for Apple's boot personalization and 1TR setup."""
import logging
import shutil

from boot_inputs import BootInputError, stub_members


GIB = 1024**3
STUB_SIZE = 4 * GIB
RECOVERY_FREE_BYTES = GIB
# APFS metadata, duplicate small plists, Finish Installation.app, verifier,
# administrator recovery records, and Apple's personalized boot objects.
BOOT_OVERHEAD_BYTES = 512 * 1024**2


def check_prepared_space(archive, profile, receipt, stub_size):
    names = stub_members(archive, profile)
    image = receipt["input"]["member"]
    if image not in names:
        raise BootInputError("Recovery size accounting lost the selected BaseSystem")
    # The encrypted input is temporary; only the decoded DMG is installed.
    content = receipt["decoded"]["size_bytes"] + sum(
        archive.getinfo(name).file_size for name in names if name != image)
    required = content + BOOT_OVERHEAD_BYTES + RECOVERY_FREE_BYTES
    if stub_size < required:
        raise BootInputError(
            "Apple boot inputs need %.2f GiB including Recovery setup reserve, but the boot container "
            "is %.2f GiB. Use an installer with a larger boot container; no partitions have changed."
            % (required / GIB, stub_size / GIB))
    logging.info("Apple boot space: selected content=%d overhead=%d recovery reserve=%d container=%d",
                 content, BOOT_OVERHEAD_BYTES, RECOVERY_FREE_BYTES, stub_size)
    return required


def check_installed_space(system):
    # All four stub volumes share this APFS container's free space. Do not
    # accidentally check the running macOS container or preparation workspace.
    free = shutil.disk_usage(system).free
    logging.info("Apple boot container free space at %s: %d bytes", system, free)
    if free < RECOVERY_FREE_BYTES:
        raise BootInputError(
            "Apple boot container has only %.0f MiB free; Recovery setup requires at least 1024 MiB. "
            "Reinstall using the updated installer. Increasing Linux space does not enlarge the boot container."
            % (free / 1024**2))
