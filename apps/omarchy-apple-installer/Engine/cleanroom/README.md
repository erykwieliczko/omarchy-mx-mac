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

`restore-image` is a Go command using the standard library's
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
tuple in normal mode. The explicit developer profile override bypasses both
the model match and host macOS version match, while requiring installed macOS
for privileged execution. It keeps the selected restore build, firmware hashes,
plan identity and partition checks unchanged. This does not establish support
for the actual host or make the selected profile's kernel compatible with it. A source build and read-only preflight are not physical install proof.

Resize planning reserves two complete sets of artifact bytes for the app handoff
and the helper import, plus the catalog's `executionScratchBytes` and a separate
1 GiB allowance for ordinary macOS writes. Execution scratch covers retained
Apple stub inputs, decoded Recovery, and engine extraction (8 GiB in the current
Apple lock). Before downloading from Apple, a separate 64 GiB preflight free-space
check covers the selected Apple ZIP and native system-image decoding while macOS still
occupies its original partition. After collecting firmware, the engine detaches
and deletes the system image, retains a verified private stub-only archive, and
deletes the large selected ZIP before entering disk preflight. Retained Apple files must
fit the execution budget with 1 GiB left for engine and transaction files.
These Apple inputs are temporary files on the target, never release artifacts.
The helper still rechecks the exact approved extent against the live disk.

### Selective Apple ZIP downloads and development cache

The bundled `j713-apple-inputs.json` pins every selected ZIP member's name,
size, SHA-256, attributes, compression and original header offset. Generate
those records from the authenticated full IPSW with `pin_apple_members.py`.
The engine uses bounded HTTP Range reads, requires exact HTTP 206 responses,
and verifies every selected member before admitting its private subset ZIP.
It does not download unrelated IPSW members. For 25G83/J713, the selected
compressed members total 11,600,027,926 bytes, rather than the full
19,772,231,540-byte IPSW. Recovery lacks the J713 garden Wi-Fi payload, so the
pinned system-image member is still required.

`assemble_engine.py --development-apple-cache` explicitly adds a private
build marker enabling `/var/db/com.omarchy.mx.installer-dev-cache`. Normal
builds omit it and always fetch from Apple. The dev cache stores only verified
selected ZIP members, keyed by the selected-member lock. Every hit is cloned
into private scratch and revalidated against the signed per-member hashes.
Successful acquisition survives subsequent preparation failure. Development
catalogs reserve the cache as well as execution workspace (20 GiB total for the
current selection); normal catalogs reserve 8 GiB. Release assembly rejects a
development cache without this extra retained-space allowance. The cache is
local development state and is never included in release assets.

Mount admission resolves filesystem aliases such as `/var` and `/private/var`
before comparing the requested directory with hdiutil's reported mount path.
Qualification must exercise the helper's `/var/db` path, not just `/Users/Shared`.

### Explicit developer model override

The app starts in automatic detection mode. The developer checkbox selects a
release profile (currently M4 MacBook Air / `apple,j713`), including on a model
that automatic detection rejects or cannot identify. Switching it clears the
plan and its approval. The selected profile is included in the authenticated
handoff identity and a version-2 candidate approval digest; the helper and
engine reject mismatched selections. Automatic approvals keep their version-1
binding unchanged.

Override bypasses only the hardware tuple gate. The engine preserves actual
sysinfo for Apple personalization and binds Recovery's product check to the
actual Mac. Boot mode, macOS baseline, signed artifacts, firmware validation,
live disk layout, existing-install refusal, and owner authorization still
apply. Selecting a profile does not supply drivers or Apple restore support
for other hardware. The app opts out of macOS relaunch at login on startup.

### Range transport throughput

Bulk ZIP reads use bounded 32 MiB read-ahead even when a member begins inside
an already cached block. Choosing the window from the remaining bytes after
that cached prefix causes repeated 1 MiB requests and connection overhead.
The regression exercises unaligned reads, byte correctness, cache bounds,
and the final partial block; small ZIP metadata reads still fetch one block.

Release assembly accepts `--payload-source-url` to reuse an unchanged,
already hosted HTTPS OS payload. Its local receipt, component profile, size,
and hash must still pass validation before the new catalog is signed.

For a cold-download test, close the installer and double-click
`Packaging/clear-installer-cache.command` (it can be copied to Desktop).
It asks for administrator access once and clears the current macOS user's
staged downloads and the shared Apple development cache. It preserves
installed systems, app settings, and diagnostic logs. `--check` lists the
scope without removing files. A later dev installation will populate the
cache again.

### Firmware ranges (0.9.5)

The normal path reads a catalog-pinned range recipe, downloads only its AEA
prefix, cluster headers and compressed segments directly from Apple, obtains
the release key from Apple's FCS service, and authenticates/decrypts each
selected segment. It reconstructs the required APFS storage spans and converts
NVRAM using the same output hashes as the full-image collector. No Apple
firmware or release keys are embedded in the recipe or installer.

`firmware_ranges.py` binds the recipe digest, Apple build, model source mapping
and final hashes to the complete signed Apple input lock. The native
`restore-image/ranges.go` reader verifies exact HTTP 206 extents and ciphertext
SHA-256, AEA root/cluster/segment authentication, plaintext checksums, compressed
storage hashes, original-file hashes, and final output hashes. The recipe pins
selected cluster MACs to the authenticated baseline; this is not a claim that
a partial download verifies the whole IPSW digest. The LZFSE/LZVN dependency is
pinned in go.mod/go.sum with its license in THIRD_PARTY_NOTICES.txt.

For J713 build 25G83, six Wi-Fi files need 2,865,066 response-body bytes across
eight ranges, plus the small FCS key request. Selected Recovery and boot ZIP
members bring the Apple component total to 1,268,127,936 bytes (about 1.27 GB),
excluding ZIP read-ahead and transport overhead. The existing Omarchy OS image
is a separate download. Both collectors still verify the complete seven-file
Wi-Fi/touchpad inventory before partition preflight.

An unavailable remote range path is logged and may fall back to the existing
fully pinned system-image path. Local recipe/output errors, cancellation and
deadline expiry stop. The conservative 64 GiB preparation space check remains
because fallback still needs the full system image. Development cache keys
include the exact selected ZIP member set, separating normal Recovery-only
selection from a full fallback selection. The standalone prototype under
`Experiments/firmware-ranges` prepares recipes from an authenticated full
baseline and supports additional files through a JSON file list.

## Installation allocation

The hard allocation minimum is the APFS stub plus the image partition minimum
(`OSInstaller.min_size`), not the larger recommended allocation. The current
image needs 39,531,315,200 bytes including boot partitions. The UI exposes the
planner’s aligned minimum and maximum through a slider and a GB entry field;
macOS resize limits and temporary download/workspace reserves still apply.
Changing the size requires a new bound plan before installation can start.

## Developer override / YOLO bring-up

The override selects a Linux payload, not an Apple hardware identity. Apple
boot identities and their complete member hashes are generated from the pinned
universal IPSW by `pin_apple_members.py`. Runtime selection matches the actual
Mac's product, device class, board and chip. There is no manually maintained
Linux hardware allowlist in override mode. A hardware identity absent from the
Apple restore build requires newer Apple inputs; Linux bring-up does not make
an incompatible Apple boot manifest valid.

The downloader selects only the actual Mac's boot members in YOLO mode. m1n1,
U-Boot, kernel and initramfs still install. Optional Linux firmware mappings are
used when available; missing mappings, extraction failures and absent device
firmware are reported and skipped. Bytes failing verification are never used.
An empty vendor-firmware archive remains valid so GRUB can still load its
vendor initramfs. Wi-Fi calibration is not a boot prerequisite. Normal mode
retains the full pinned Linux firmware requirement and verified fallback.

Apple boot inputs, disk boundaries, required payload hashes, read-back and
machine-owner authorization remain mandatory. `bless` output now goes through
the helper's private, bounded, credential-redacted diagnostic pipes.
