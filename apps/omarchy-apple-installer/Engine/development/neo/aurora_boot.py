# SPDX-License-Identifier: MIT
"""Pinned Aurora boot bundle, independent of the upstream engine and OS ZIP."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import subprocess
import tarfile
import urllib.parse
import urllib.request
import uuid
import zipfile

from boot_inputs import BootInputError

EFI_PATH = "aurora/boot/boot.bin"
MAX_ARCHIVE = 384 * 1024**2
MAX_CONTENTS = 448 * 1024**2
MAX_TAR = 456 * 1024**2
KERNEL_NAME = "aurora-silicon-dirtyroom-J700"
KERNEL_RELEASE = "7.1.6-aurora-silicon-dirtyroom-j700.1"
KERNEL_UKI = "EFI/Linux/omarchy_" + KERNEL_NAME + ".efi"
KERNEL_ROOT = "aurora/kernel/" + KERNEL_RELEASE + "/root.tar.zst"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def checked_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "f005.backblazeb2.com"
            or not parsed.path.startswith("/file/omarchymacexperimental/aurora/boot/")
            or parsed.query or parsed.fragment):
        raise BootInputError("Aurora boot bundle has an invalid download URL")
    return value


class AuroraRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        checked_url(url)
        return super().redirect_request(request, fp, code, message, headers, url)


def read_archive(path):
    """Decode with the bundled tool; read bounded files without extracting paths."""
    with open(path, "rb") as reader:
        magic = reader.read(4)
    if magic == b"\x28\xb5\x2f\xfd":
        decoder = Path(__file__).with_name("restore-image-tool")
        try:
            result = subprocess.run([str(decoder), "aurora-tar", str(path)],
                                    check=True, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as error:
            raise BootInputError("Cannot decode Aurora boot bundle") from error
        if len(result.stdout) > MAX_TAR:
            raise BootInputError("Aurora decoded archive exceeds limit")
        source = io.BytesIO(result.stdout)
    else:
        source = open(path, "rb")
    files, total = {}, 0
    try:
        with source, tarfile.open(fileobj=source, mode="r:") as archive:
            for entry in archive:
                name, size = entry.name, entry.size
                parts = PurePosixPath(name)
                if (not name or parts.is_absolute() or ".." in parts.parts
                        or str(parts) != name or "\\" in name or name in files
                        or not entry.isfile() or entry.issparse()
                        or not 0 <= size <= MAX_ARCHIVE or total + size > MAX_CONTENTS
                        or len(files) >= 128):
                    raise BootInputError("Unsafe Aurora boot archive member")
                data = archive.extractfile(entry).read(size + 1)
                if len(data) != size:
                    raise BootInputError("Aurora boot member size mismatch")
                files[name] = data
                total += size
    except (tarfile.TarError, UnicodeError) as error:
        raise BootInputError("Invalid Aurora boot archive") from error
    return files


def verify_bundle(path, pin):
    if not 0 < Path(path).stat().st_size <= MAX_ARCHIVE:
        raise BootInputError("Aurora boot bundle size is invalid")
    data = Path(path).read_bytes()
    if len(data) != pin["size_bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise BootInputError("Aurora boot bundle download failed verification")
    files = read_archive(path)
    manifest_data = files.pop("manifest.json", b"")
    if hashlib.sha256(manifest_data).hexdigest() != pin["manifest_sha256"]:
        raise BootInputError("Aurora boot manifest failed verification")
    manifest = json.loads(manifest_data)
    if (manifest.get("schema_version") != 1 or manifest.get("kind") != "aurora-silicon-boot"
            or manifest.get("devices") != ["apple,j700"] or manifest.get("efi_path") != EFI_PATH
            or manifest.get("stage1") != "boot/m1n1-stage1.bin"
            or manifest.get("stage2") != "boot/boot.bin"
            or set(files) != set(manifest["files"])):
        raise BootInputError("Aurora boot manifest is incompatible with Neo")
    for name, record in manifest["files"].items():
        if (len(files[name]) != record["size_bytes"]
                or hashlib.sha256(files[name]).hexdigest() != record["sha256"]):
            raise BootInputError("Aurora boot component failed verification")
    if not all(files.get(manifest[key]) for key in ("stage1", "stage2")):
        raise BootInputError("Aurora boot bundle is incomplete")
    kernel = manifest.get("kernel")
    if kernel is not None:
        if (kernel.get("release") != KERNEL_RELEASE or kernel.get("menu_name") != KERNEL_NAME
                or kernel.get("uki") != "kernel/kernel.efi"
                or kernel.get("root") != "kernel/root.tar.zst"
                or not files.get(kernel["uki"], b"").startswith(b"MZ")
                or not files.get(kernel["root"], b"").startswith(b"\x28\xb5\x2f\xfd")):
            raise BootInputError("Aurora kernel bundle is incompatible with Neo")
    return manifest, files


def acquire_bundle(directory, pin, *, opener=None):
    size = pin["size_bytes"]
    if pin.get("schema_version") != 1 or type(size) is not int or not 0 < size <= MAX_ARCHIVE:
        raise BootInputError("Invalid Aurora boot bundle pin")
    url = checked_url(pin["url"])
    opener = opener or urllib.request.build_opener(AuroraRedirects())
    path = Path(directory) / "aurora-boot.tar.zst"
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise BootInputError("Unexpected Aurora boot download response")
        with path.open("xb") as writer:
            digest, received = hashlib.sha256(), 0
            while chunk := response.read(min(65536, size + 1 - received)):
                writer.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if received > size:
                    raise BootInputError("Aurora boot bundle download is oversized")
    if received != size or digest.hexdigest() != pin["sha256"]:
        raise BootInputError("Aurora boot bundle download failed verification")
    return verify_bundle(path, pin)


def stage1_image(base, esp_uuid):
    identifier = str(uuid.UUID(esp_uuid)).upper()
    if (uuid.UUID(identifier).int == 0
            or re.search(rb"chainload=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", base)
            or b"Chainloading files not supported in this build!" in base):
        raise BootInputError("Invalid Aurora stage-1 binding")
    variables = (f"chosen.asahi,efi-system-partition={identifier}\n"
                 f"chainload={identifier};{EFI_PATH}\n").encode("ascii")
    result = base + variables + b"\0" * 4
    return result + b"\0" * (-len(result) % 16384)


class AuroraOSArchive:
    """Add Aurora ESP files and select its kernel, preserving the stock fallback."""
    def __init__(self, original, manifest, files, pin):
        self.original = original
        self.added = {
            "esp/aurora/": b"", "esp/aurora/boot/": b"",
            "esp/" + EFI_PATH: files[manifest["stage2"]],
            "esp/aurora/boot/bundle.json": canonical({"archive_sha256": pin["sha256"],
                                                     "manifest": manifest}),
        }
        if any(name == "esp/aurora" or name.startswith("esp/aurora/")
               for name in original.namelist()):
            raise BootInputError("Upstream OS archive already owns the Aurora ESP path")
        if "kernel" in manifest:
            kernel = manifest["kernel"]
            menu = original.read("esp/limine.conf").decode("utf-8")
            if ("/+Omarchy\n" not in menu or "//linux-aurora\n" not in menu
                    or KERNEL_NAME in menu or "rd.luks." in menu):
                raise BootInputError("Original boot menu is not the admitted plaintext OS menu")
            menu = re.sub(r"^default_entry:.*$", "default_entry: Omarchy/" + KERNEL_NAME,
                          menu, count=1, flags=re.MULTILINE)
            if "default_entry: Omarchy/" + KERNEL_NAME not in menu:
                raise BootInputError("Original boot menu has no default entry")
            cmdlines = re.findall(r"^\s+cmdline: (.+)$", menu, flags=re.MULTILINE)
            if len(cmdlines) != 1:
                raise BootInputError("Original kernel command line is ambiguous")
            menu += ("\n  //" + KERNEL_NAME + "\n  comment: Aurora Silicon J700: " + KERNEL_RELEASE
                     + "\n  comment: kernel-id=" + KERNEL_NAME
                     + "\n  protocol: efi\n  path: boot():/" + KERNEL_UKI
                     + "\n  cmdline: " + cmdlines[0]
                     + " idle=nop arm64.nowfxt firmware_class.path=/vendorfw\n")
            self.added.update({
                "esp/limine.conf": menu.encode(),
                "esp/" + KERNEL_UKI: files[kernel["uki"]],
                "esp/aurora/kernel/": b"",
                "esp/aurora/kernel/" + KERNEL_RELEASE + "/": b"",
                "esp/" + KERNEL_ROOT: files[kernel["root"]],
            })
        self.infos = {}
        for name, data in self.added.items():
            info = zipfile.ZipInfo(name)
            info.file_size = len(data)
            info.external_attr = ((stat.S_IFDIR | 0o755) if name.endswith("/")
                                  else (stat.S_IFREG | 0o644)) << 16
            self.infos[name] = info

    def infolist(self):
        return [info for info in self.original.infolist() if info.filename not in self.infos] + list(self.infos.values())

    def namelist(self):
        return [name for name in self.original.namelist() if name not in self.infos] + list(self.infos)

    def getinfo(self, name):
        return self.infos[name] if name in self.infos else self.original.getinfo(name)

    def open(self, member, mode="r", pwd=None, *, force_zip64=False):
        name = member.filename if isinstance(member, zipfile.ZipInfo) else member
        if mode != "r":
            raise BootInputError("Aurora OS archive is read-only")
        if name in self.added:
            return io.BytesIO(self.added[name])
        return self.original.open(member, mode, pwd, force_zip64=force_zip64)

    def read(self, member, pwd=None):
        with self.open(member, pwd=pwd) as reader:
            return reader.read()

    def close(self):
        self.original.close()


def configure_aurora(resource_directory):
    import copy
    import m1n1
    import omarchy_asahi
    from apple_inputs import progress

    pin = json.loads((Path(resource_directory) / "aurora-bundle.json").read_text())
    original_adapter = omarchy_asahi.AsahiStage1Adapter
    active = {}

    class AuroraAdapter(original_adapter):
        def preflight(self, plan):
            if self.preflight_complete:
                return
            self.aurora_workspace = tempfile.TemporaryDirectory(prefix="aurora-boot-")
            progress("Downloading and verifying Aurora Silicon bootloaders and kernel...")
            manifest, files = acquire_bundle(self.aurora_workspace.name, pin)
            if (plan.engine_digest != "sha256:" + manifest["engine_sha256"]
                    or plan.payload_digest != "sha256:" + manifest["os_sha256"]):
                raise BootInputError("Aurora boot bundle does not match the selected engine and OS")
            self.aurora_base = files[manifest["stage1"]]
            # This validates the original OS ZIP before adding separate Aurora files.
            super().preflight(plan)
            self.osins.pkg = AuroraOSArchive(self.osins.pkg, manifest, files, pin)
            # The admitted OS uses a 500 MiB ESP. Reserve a second UKI for atomic updates plus 64 MiB for firmware,
            # FAT metadata and receipts; fail before any partition operation.
            esp_bytes = sum(info.file_size for info in self.osins.pkg.infolist()
                            if info.filename.startswith("esp/"))
            update_bytes = len(files[manifest["kernel"]["uki"]]) if "kernel" in manifest else 0
            if esp_bytes + update_bytes > 436 * 1024**2:
                raise BootInputError("Aurora kernel and original boot files exceed ESP capacity")
            self.osins.template = copy.deepcopy(self.osins.template)
            self.osins.template["next_object"] = EFI_PATH
            active["base"] = self.aurora_base

        def _installed_evidence(self, plan):
            evidence = super()._installed_evidence(plan)
            expected = stage1_image(self.aurora_base, self.osins.efi_part.uuid)
            path = Path(self.installer.ins.boot_obj_path)
            if path.is_symlink() or path.stat().st_size != len(expected) or path.read_bytes() != expected:
                raise BootInputError("Installed Aurora stage 1 failed verification")
            # The ESP tree digest includes boot.bin and the complete bundle receipt.
            # A retry with a different bundle therefore fails the existing checkpoint.
            return evidence

    def build_stage1(source, destination, variables):
        if (source != "boot/m1n1.bin" or "base" not in active
                or not isinstance(variables, list) or len(variables) != 2
                or not variables[0].startswith("chosen.asahi,efi-system-partition=")):
            raise BootInputError("Unexpected Aurora stage-1 build request")
        identifier = variables[0].split("=", 1)[1]
        if variables[1] != f"chainload={identifier};{EFI_PATH}":
            raise BootInputError("Aurora stage-1 chainload target differs from selected bundle")
        Path(destination).write_bytes(stage1_image(active["base"], identifier))

    omarchy_asahi.AsahiStage1Adapter = AuroraAdapter
    m1n1.build = build_stage1
