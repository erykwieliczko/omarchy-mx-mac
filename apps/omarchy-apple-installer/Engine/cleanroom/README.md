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
the native restore decoder, and exact OS metadata. `build_restore_package.py`
retains the complete selected Apple stub closure; `build_payload.py` seals the
verified disposable filesystem images and the embedded disk-boot bundle.
`firmware.py` reads the six generic Broadcom files from the full macOS system
volume at the exact admitted version/build. Recovery carries incomplete links
for this chipset. The model profile defines their source/output mapping; no
machine calibration or installed Linux archive is used. The touchpad blob is
converted from the selected Apple IPSW's Multitouch component. All seven output
hashes must match both the root image and early initramfs.
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

Inspection may identify the model without root access to bputil. Execution
requires positively identified macOS 26.6.2 and revalidates the complete model
tuple. A source build and read-only preflight are not physical install proof.

Resize planning reserves two complete sets of artifact bytes for the app handoff
and the helper import, plus the catalog's `executionScratchBytes` and a separate
1 GiB allowance for ordinary macOS writes. The scratch budget must cover peak
engine extraction and temporary Recovery files, including the restore ZIP,
both encrypted Recovery copies, and the decoded image. Qualify it with the exact
engine/payload in a file-only preflight before sealing a release. Cleanroom
catalogs without a positive budget are rejected. These bytes remain available
to macOS during installation; they are not part of the requested Linux extent.
The helper still rechecks the exact approved extent against the live disk.
