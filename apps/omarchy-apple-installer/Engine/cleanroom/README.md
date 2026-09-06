# Cleanroom boot inputs

These components implement a private fresh-install path for MacBook Air M4
J713 (Mac16,12), using the accepted cleanroom source graph. They do not enable
any public model or claim physical qualification. Each new candidate requires owner smoke testing of installation and paired
Recovery boot.

`profiles/j713.json` pins model identity, firmware version and the accepted
cleanroom source graph. It contains no machine UUIDs or host paths. The signed
catalog and engine artifact descriptor must authenticate the profile before
it can govern an installation.

`boot_inputs.py` validates exact IPSW identity and Recovery component references,
and assembles an admitted raw stage 1 with the new ESP UUID and complete-object
16 KiB alignment. The caller must establish the source's `RELEASE=1
CHAINLOADING=1` build provenance. Manifest reference validation alone is not
Apple signature or decoded Recovery authentication.

`restore-image` is a dependency-free Go command using the standard library's
HPKE support and macOS `/usr/bin/aea`. It validates the input SHA-256, stages
private bytes, obtains the public release key from Apple's HTTPS FCS service,
authenticates the wrapped archive key, and asks Apple's utility to authenticate
and decode the archive. It checks that macOS recognizes the resulting disk
image and publishes it without overwriting existing output. It does not mount
the image, change boot policy, or establish paired Recovery boot acceptance.

The AEA metadata/FCS envelope format and native tool invocation are documented
by the [ipsw project](https://blacktop.github.io/ipsw/docs/guides/aea/) and its
[AEA reader](https://github.com/blacktop/ipsw/blob/master/pkg/aea/aea.go).
Cryptographic primitives come from Go's `crypto/hpke`; no firmware code is
disassembled or interpreted.

Validation:

```sh
python3 -m unittest discover -s apps/omarchy-apple-installer/Engine/tests -p 'test_cleanroom_*.py'
cd apps/omarchy-apple-installer/Engine/cleanroom/restore-image
go test ./...
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -o /path/to/omarchy-restore-image .
```

The build requires Go 1.26 or newer. A candidate release must additionally pin
the exact toolchain and executable digest in its engine source/artifact lock.
The decoder's command line is:

```sh
omarchy-restore-image INPUT_AEA INPUT_SHA256 OUTPUT_DMG
```

`stage_sources.py` applies the locked transaction library and isolated factory
patches. `assemble_engine.py` adds admitted Python runtime bytes, raw stage one,
the native restore decoder, and exact OS metadata. `build_payload.py` seals
verified firmware-free filesystem images and the disk-boot bundle. The older
`build_restore_package.py` remains a private build-time inspection utility;
its Apple archive is not an input to the distributable payload or engine.
`profiles/j713-apple-inputs.json` records Apple's official restore URL, full
IPSW SHA-256 and length, selected system-image member, and seven converted
firmware hashes. Engine metadata authenticates the same complete lock.
`apple_inputs.py` downloads that exact build over HTTPS directly from Apple,
validates it while streaming, and decodes and mounts the system image read-only
in private scratch space. It rejects redirects outside the admitted Apple CDN.
`firmware.py` reads the six generic Broadcom files from this downloaded system
volume at the exact admitted version/build. Recovery carries incomplete links
for this chipset. The model profile defines their source/output mapping; no
machine calibration or installed Linux archive is used. The touchpad blob is
converted from the selected Apple IPSW's Multitouch component. All seven output
hashes must match the lock before any partition changes. There is no fallback
to firmware from the installed macOS. Network, decode, mount or hash failures
stop during preflight. Scratch cleanup preserves files when image detach is
unconfirmed, rather than recursively traversing a mounted image.

The installer writes the converted firmware to the new ESP as `firmware.cpio`
and `firmware.tar`. U-Boot loads GRUB from the ESP selected by m1n1's generated
partition UUID; GRUB derives its device from `$cmdpath`, loads the firmware CPIO
after its firmware-free embedded initramfs, and passes
`firmware_class.path=/vendorfw`. The existing first-boot service imports the
same firmware into the installed root filesystem. The distributed root, boot,
initramfs and factory snapshot contain no vendor firmware blobs. The image
recipe removes generic `linux-firmware` packages and package caches, then copies
only live files into fresh filesystems to discard deleted firmware blocks.
`build_release.py` requires an explicit HTTPS artifact base URL and a qualified
`--execution-scratch-bytes` budget, signs a private catalog and includes only the inspection engine by default. Metadata and the
OS payload use the normal verified downloader. Offline payload bundling needs
an explicit `--bundle-payload` flag. The builder never publishes a release or
retains the temporary catalog signing key.

The filesystem recipe lives in the ISO repository at `builder/cleanroom/`.
The root image keeps Omarchy's provisioning services, replaces stock boot
writers with `linux-omarchy-j713`, and uses a model-level image UUID. GRUB embeds
the matching kernel and initramfs. The current filesystem recipe boots to the graphical login and Omarchy using
Mesa software rendering and the patched Aquamarine backend. The kernel does
not enable the Asahi GPU driver. Kernel/boot updates require a new
qualified complete boot bundle; the stock update-m1n1 path is removed.

Assign each candidate its own engine version and payload basename when assembling:

```sh
python3 Engine/cleanroom/assemble_engine.py /path/to/transaction-checkout \
  /path/to/inputs /path/to/new-engine --version v0.2.0-cleanroom.1 \
  --payload-name omarchy-j713-private-0.9.0.zip
```

The payload sealer checks the exact root/initramfs verification and boot receipts
before compression, then checks the streamed file digests and archive CRCs.
Release assembly requires matching engine/metadata and payload receipts and the
same component revisions throughout before signing its private catalog.

Inspection may identify the model without root access to bputil. Execution
requires positively identified macOS 26.6.2 and revalidates the complete model
tuple. A source build and read-only preflight are not physical install proof.

Resize planning reserves two complete sets of artifact bytes for the app handoff
and the helper import, plus the catalog's `executionScratchBytes` and a separate
1 GiB allowance for ordinary macOS writes. Execution scratch covers retained
Apple stub inputs, decoded Recovery, and engine extraction (8 GiB in the current
Apple lock). Before downloading from Apple, a separate 64 GiB preflight free-space
check covers the full IPSW and native system-image decoding while macOS still
occupies its original partition. After collecting firmware, the engine detaches
and deletes the system image, retains a verified private stub-only archive, and
deletes the full IPSW before entering disk preflight. Retained Apple files must
fit the execution budget with 1 GiB left for engine and transaction files.
These Apple inputs are temporary files on the target, never release artifacts.
The helper still rechecks the exact approved extent against the live disk.
