# SPDX-License-Identifier: MIT
"""Package admitted Neo boot binaries and their corresponding sources, without rebuilding."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

VERSION = "2026.09.25.1"
PREFIX = "https://f005.backblazeb2.com/file/omarchymacexperimental/aurora/boot/" + VERSION + "/"
M1N1 = "1ab1683c8ef21b8e6f74253782a9c5af44220eee"
UBOOT = "eb052fe3f2871bf8cdddf25ec600ef7dfec0269f"
LINUX = "6ba27a80f991f1627f41fa71aa241d600246e948"
ARTWORK = "80d14f8b6f485b310e305a84b4b806361518ddd1"
INPUTS = {
    "boot/m1n1-stage1.bin": ("inputs/m1n1-stage1-base.bin", "f4bd70b9803ddea9cd1d9e411fafacca71f268a1a3573e23556a092359f3b2bf"),
    "boot/boot.bin": ("boot/j700/boot.bin", "49911e9140d021df496740c6d37109c0ffe91be9a5ad8d425cab51f96e9807c3"),
    "build/u-boot.config": ("inputs/u-boot.config", "c141f871a24d8785423a63caca96bf0662ec968db43df8fa80441cec95eae00b"),
    "build/u-boot-receipt.json": ("inputs/u-boot-receipt.json", "87e791c9ed00e851412f038981c76f0972e4cd6f734f02e3e8be3f2923287078"),
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def record(data):
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def git(repository, *arguments):
    return subprocess.check_output(["git", "-c", "safe.directory=" + str(repository),
                                    "-C", str(repository), *arguments])


def archive(output, name, files):
    plain = output / (name + ".tar")
    with tarfile.open(plain, "w", format=tarfile.USTAR_FORMAT) as writer:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size, info.mode = len(data), 0o644
            writer.addfile(info, io.BytesIO(data))
    compressed = output / (name + ".tar.zst")
    subprocess.run(["zstd", "-q", "-19", "-T1", str(plain), "-o", str(compressed)], check=True)
    plain.unlink()
    return {"url": PREFIX + compressed.name, **record(compressed.read_bytes())}


def build(args):
    args.output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, (path, digest) in INPUTS.items():
        data = (args.artifacts / path).read_bytes()
        if record(data)["sha256"] != digest:
            raise ValueError("Pinned boot input changed: " + path)
        files[name] = data
    source_files = {}
    for name, repository, revision, paths in (
        ("m1n1", args.m1n1, M1N1, []), ("u-boot", args.uboot, UBOOT, []),
        ("artwork", args.artwork, ARTWORK, []),
        ("linux-dtb", args.linux, LINUX,
         ["arch/arm64/boot/dts/apple", "include/dt-bindings", "LICENSES", "COPYING"]),
    ):
        data = git(repository, "archive", "--format=tar", revision, *paths)
        source_files[name + ".tar"] = data
        # Notices are also available alongside the binary, without source download.
        if name in ("m1n1", "u-boot", "artwork"):
            with tarfile.open(fileobj=io.BytesIO(data)) as reader:
                for entry in reader:
                    if entry.isfile() and (entry.name == "LICENSE" or entry.name.startswith(
                            ("Licenses/", "3rdparty_licenses/")) or entry.name == "logos/LICENSE"):
                        files["licenses/" + name + "/" + entry.name] = reader.extractfile(entry).read()
    for name in ("build_u_boot.py", "build_boot.py", "build_neo_dtb.py"):
        source_files["recipes/" + name] = (args.recipes / name).read_bytes()
    source_files["recipes/bootloader/u-boot.config"] = files["build/u-boot.config"]
    source_files["README.txt"] = (
        "Aurora Silicon boot inputs " + VERSION + "\n\n"
        "Source revisions: m1n1 " + M1N1 + "; U-Boot " + UBOOT + "; Linux DTs " + LINUX + ".\n"
        "Extract each component tar into its own directory; artwork belongs in m1n1/artwork.\n"
        "m1n1 stage 1: make RELEASE=1 CHAINLOADING=1 USE_CLANG=1 BUILDSTD=1\n"
        "m1n1 stage 2: make RELEASE=1 USE_CLANG=1 BUILDSTD=1 (use a separate build tree).\n"
        "U-Boot: use recipes/build_u_boot.py with the pinned git checkout; it archives that revision,\n"
        "applies the supplied config, and builds with aarch64-linux-gnu GCC. The same make steps\n"
        "work on the supplied u-boot.tar source. Python, make, GCC/binutils, bison, flex and dtc are needed.\n"
        "Neo DT: recipes/build_neo_dtb.py; J713 DT: Linux arch/arm64/boot/dts/apple/t8132-j713.dts.\n"
        "Combined stage 2: recipes/build_boot.py disk_payload(stage2, [j713_dtb, j700_dtb], uboot).\n"
        "No kernel or EFI executable is shipped by this bundle. Binaries are reused byte-for-byte\n"
        "from installer-2.0.3; rebuilding with a different toolchain need not reproduce their hashes.\n"
    ).encode()
    source_pin = archive(args.output, "aurora-silicon-boot-sources-" + VERSION, source_files)
    manifest = {
        "schema_version": 1, "kind": "aurora-silicon-boot", "version": VERSION,
        "devices": ["apple,j700"], "stage1": "boot/m1n1-stage1.bin", "stage2": "boot/boot.bin",
        "efi_path": "aurora/boot/boot.bin",
        "engine_sha256": "ecb61645a9c75ba733425fb300b8b53b09f9dbc297a86acce1e0ee41f36e32e5",
        "os_sha256": "0920b622f67295c6a3fa7a73ba942fa6b170b4f9504a173054c2f69683909aa4",
        "sources": {"archive": source_pin,
                    "m1n1": {"repository": "https://github.com/erykwieliczko/m1n1-alpha", "revision": M1N1},
                    "u_boot": {"repository": "https://github.com/erykwieliczko/u-boot-alpha", "revision": UBOOT},
                    "device_trees": {"j700": "u-boot doc/board/apple/j700-first-light/boot.dts",
                                     "j713_linux_revision": LINUX}},
        "files": {name: record(data) for name, data in sorted(files.items())},
    }
    files["manifest.json"] = canonical(manifest)
    pin = {"schema_version": 1,
           **archive(args.output, "aurora-silicon-boot-j700-" + VERSION, files),
           "manifest_sha256": record(files["manifest.json"])["sha256"]}
    for name, value in (("bundle.json", pin), ("manifest.json", manifest), ("sources.json", source_pin)):
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(pin, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("artifacts", "m1n1", "uboot", "linux", "artwork", "recipes", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    build(parser.parse_args())
