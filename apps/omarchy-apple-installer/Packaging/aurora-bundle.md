# Aurora Silicon boot bundle

The experimental Neo profile adds a third download without rewriting either the
upstream engine archive or OS ZIP. Version `2026.09.25.2` retains the admitted stage-1 m1n1 base and adds the J700
kernel, complete modules, initial initramfs/UKI, and matching stage-2 m1n1/U-Boot.
Limine selects **aurora-silicon-dirtyroom-J700**, retaining the stock kernel entry.
The Linux store is `/aurora`; Mesa is unchanged. This is a development candidate,
not a production-qualified Neo release.
Automatic selection applies only to `apple,j700` in development builds. Manual
Neo overrides use this same bundle. M1/M2 and the protected M4 gate are unchanged.

## Format and verification

`aurora-silicon-boot-j700-2026.09.25.2.tar.zst` contains regular files only:

- `manifest.json`: schema, version, device allowlist, engine and OS digests,
  boot paths, component hashes/sizes, source revisions and source archive URL;
- `boot/m1n1-stage1.bin`: unbound stage-1 base;
- `boot/boot.bin`: stage 2, full J700 device tree, compressed U-Boot;
- `kernel/kernel.efi` and `kernel/root.tar.zst`: initial boot image and pinned Linux store;
- build configuration, receipt and license notices.

`Engine/development/neo/aurora-bundle.json` pins the public Backblaze URL, archive
size/SHA-256 and manifest SHA-256. Before disk changes, the engine downloads into
a private temporary directory and checks all pins, the Neo allowlist, the selected
engine/OS digests and every member hash. The bundled native decoder limits zstd memory and output; the Python tar reader
bounds member sizes and rejects duplicate paths, traversal, sparse files and
links. It reads members into memory without extracting archive-controlled paths.
The decoder is built from the existing restore-image Go module with pinned
`github.com/klauspost/compress` v1.18.5; no system zstd or Homebrew is required.

The OS archive view adds Aurora boot files, UKI and initial root payload, and
updates `esp/limine.conf` to select the new entry. The upstream archives, root and
boot images, and stock kernel remain intact. Admission reserves ESP room for
firmware and a second UKI during atomic updates. Stage 1 is bound to the new installation's actual
ESP UUID and `aurora/boot/boot.bin`, then padded to 16 KiB. U-Boot loads the
existing `/EFI/BOOT/BOOTAA64.EFI`. The existing EFI loader remains in use. The new boot chain is experimental;
compilation and archive validation are not physical boot qualification.
The normal `update-m1n1` path does not overwrite the separate Aurora stage 2.

Installed verification checks stage 1 byte-for-byte and the added ESP files via
the engine's existing readback. The ESP receipt binds the archive and manifest
into the installation checkpoint, so Recovery retry cannot silently switch
bundles. Fresh Neo retries initialize installed paths before verifying them.

## Packaging and publication

The kernel recipe and first-boot/update lifecycle are described in
[kernel/README.md](kernel/README.md). The kernel packager creates deterministic
archive metadata and separate complete corresponding-source downloads, including
Linux, m1n1, U-Boot, artwork, configurations, receipts and recipes. Both archives
must remain publicly available. The installer downloads only the binary archive.

Version `2026.09.25.1` remains the immutable bootloader-only baseline, created by
`build-aurora-bundle.py`. Kernel candidates use `kernel/build-bundle.py` and a new
version/path; never overwrite an admitted release. Upload both generated archives,
verify their public download hashes, and copy `bundle.json` into the development
pin before building the app. No public stable catalog or M1/M2 payload changes.
