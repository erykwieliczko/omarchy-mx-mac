# SPDX-License-Identifier: MIT
"""Stage a cleanroom engine from locked Git objects and reviewed extensions."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from boot_inputs import BootInputError


def stage_sources(checkout, destination):
    engine = Path(__file__).resolve().parent.parent
    lock = json.loads((engine / "cleanroom/source-lock.json").read_text())
    legacy = json.loads((engine / "source-lock.json").read_text())
    if lock["status"] != "development-source-only" or lock["public_release_authorized"]:
        raise BootInputError("unexpected cleanroom source lock state")
    revision = legacy["upstream_installer"]["commit"]
    if revision != lock["transaction_source_revision"]:
        raise BootInputError("transaction source revision changed")
    subprocess.run(["git", "-C", str(checkout), "cat-file", "-e", revision + "^{commit}"],
                   check=True)
    for record in lock["files"]:
        source = engine / record["path"]
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
            raise BootInputError("locked engine source changed: " + record["path"])
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BootInputError("source staging destination already exists")
    with tempfile.TemporaryDirectory(prefix=".engine-source-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        archive = temporary / "source.tar"
        with archive.open("xb") as writer:
            subprocess.run(["git", "-C", str(checkout), "archive", revision],
                           stdout=writer, check=True)
        tree = temporary / "tree"
        tree.mkdir()
        with tarfile.open(archive) as reader:
            reader.extractall(tree, filter="data")
        subprocess.run(["git", "-C", str(tree), "apply",
                        str(engine / legacy["downstream_overlay"]["patch"]["path"])], check=True)
        for record in legacy["downstream_overlay"]["files"]:
            # m1n1 is built separately from the cleanroom graph, never this
            # transaction library's historical firmware submodule.
            if not record["destination"].startswith(("src/", "tests/")):
                continue
            target = tree / record["destination"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(engine / record["path"], target)
        for patch in lock["patches"]:
            subprocess.run(["git", "-C", str(tree), "apply", str(engine / patch)], check=True)
        (tree / "src/main.py").rename(tree / "src/asahi_main.py")
        for name in ("main.py", "adapter.py", "boot_inputs.py", "boot_builds.py", "boot_space.py", "recovery.py", "firmware.py", "firmware_archive.py", "firmware_ranges.py", "apple_inputs.py", "apple_ranges.py"):
            shutil.copyfile(engine / "cleanroom" / name, tree / "src" / name)
        shutil.copytree(engine / "cleanroom/neo_wifi", tree / "src/neo_wifi",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(engine / "cleanroom/profiles", tree / "src/cleanroom/profiles")
        shutil.copyfile(engine / "cleanroom/step2.sh", tree / "src/cleanroom/step2.sh")
        shutil.copyfile(engine / "cleanroom/THIRD_PARTY_NOTICES.txt", tree / "src/cleanroom/THIRD_PARTY_NOTICES.txt")
        (tree / "cleanroom-source-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        tree.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(stage_sources(args.checkout.resolve(), args.destination.resolve()))
