# J713 installed boot and APFS reference

Inspected 2026-09-05 via `root@mac-m4`, at the owner's request. This is a historical installation record, not an executable installation recipe.

Later that day, the owner explicitly authorized removing Fedora, its ESP and the m1n1 APFS stub. All three partitions were removed, and macOS expanded to 245,107,195,904 bytes while preserving shared ISC and final System Recovery. The identities and files below describe the pre-removal installation; future installers must discover the current layout and provision their own boot environment.

The subsequent reboot succeeded. Read-only verification confirmed the original sealed macOS volume group, exactly three remaining physical partitions, and unchanged ISC/System Recovery identities and extents. Private cleanup logs and verification are retained at `/home/council/omarchy/mac-m4-cleanup-20260905/`.

The target reported `Mac16,12`, `J713`, and `Apple M4`. It was running macOS 26.6.2 (25G83) during inspection. The prior cleanroom handoff records Fedora disk-boot acceptance; no new Linux boot or runtime validation was performed here.

## Owner's stage-1 direction

The owner deliberately retained an older working stage 1 on this machine. New installations should use the newest accepted cleanroom m1n1 revision, built specifically with `RELEASE=1 CHAINLOADING=1`, with installation-specific variables, four terminating zero bytes and complete-object 16 KiB alignment. Pin the selected source revision and resulting artifact digest. The accepted source at this inspection was `d403081ee7ddfc3987cf0967c5a23a529f830ee5`; recheck the accepted branch when preparing a future candidate. Do not select the unaccepted behavior-experiment branches.

The installed stage 1 below is historical evidence, not the artifact to ship. This audit did not replace it, run a 1TR script, change boot policy, or reboot.

## Partition map

The internal SSD has 251,000,193,024 bytes and 4,096-byte logical blocks. GPT LBAs below use those 4 KiB blocks, not 512-byte units. Disk identifiers are observations and must be rediscovered before any future operation.

| Partition | Start LBA | Length LBA | Partition bytes | Purpose |
| --- | ---: | ---: | ---: | --- |
| `disk0s1` | 6 | 128000 | 524288000 | Apple_APFS_ISC: iBootSystemContainer |
| `disk0s2` | 128006 | 29920312 | 122553597952 | Existing macOS APFS container |
| `disk0s3` | 30048318 | 2441406 | 9999998976 | Linux boot stub APFS container |
| `disk0s4` | 32489724 | 244140 | 999997440 | FAT ESP, `M1N1ESP` |
| `disk0s5` | 32733864 | 27234766 | 111553601536 | Linux Btrfs root filesystem |
| `disk0s6` | 59968630 | 1310709 | 5368664064 | Apple_APFS_Recovery: final System Recovery container |

`diskutil info` reports a smaller filesystem total for the FAT ESP (997,920,768 bytes); the table uses the GPT partition length. These are different measurements.

The Linux partition's primary Btrfs superblock was read with `O_RDONLY` at partition-relative byte offset 65,536. Its magic is `_BHRfS_M`, filesystem UUID is `ae2241c3-a92f-46e0-a40a-4a269ed327a6`, and total size is 111,553,601,536 bytes. The prior Linux log identifies root subvolume `@`. macOS was not used to mount or enumerate Btrfs files.

The shared ISC contains `iSCPreboot`, `xART`, `Hardware`, and `Recovery` volumes. The final System Recovery container contains `Recovery` and `Update`. They are separate from the Linux stub's own paired Recovery volume. The macOS container retains its System/Data volume group plus Preboot, Recovery, Update and VM.

## Linux stub APFS identities

Container: `4CA76A4B-FF84-425E-9AFF-8B9597027231` on GPT partition `5A97B938-0FCA-4A0C-A2B5-7B8577CB083D`.

| Role | Observed device | Name | UUID | Approx. APFS consumed bytes |
| --- | --- | --- | --- | ---: |
| Data | `disk3s1` | m1n1 - Data | `5FEACC24-5681-4669-8FF1-BA53D157528D` | 8290304 |
| System | `disk3s2` | m1n1 | `FDA96E43-D480-41BF-92D3-A56EA94603C8` | 90112 |
| Preboot | `disk3s3` | Preboot | `32D1871B-B43B-484A-892C-5D10A63E23A3` | 298852352 |
| Recovery | `disk3s4` | Recovery | `BE7CE396-7CE2-4172-8C83-9BE77815677C` | 1303011328 |

The System and Data volumes form volume group `5FEACC24-5681-4669-8FF1-BA53D157528D` (abbreviated `VG` below). Neither is sealed, and neither had APFS snapshots. FileVault was reported as off. Space is shared across the container; the 10 GB capacity is not a separate allocation for each volume.

ESP GPT PARTUUID: `4BFE423A-75F2-4D77-9F53-28523848C31B`. Linux GPT PARTUUID: `FA0D29B1-85BF-4604-B48B-1C6C79D337E3`. These and the Btrfs UUID are installation data, not model constants.

## Where the boot files actually live

| Location | Observed contents and role |
| --- | --- |
| Stub System | `System/Library/CoreServices/{PlatformSupport,SystemVersion}.plist`, `usr/standalone/bootcaches.plist`, installation note and an older `RUN-ME-IN-1TR.sh`. Tiny identification/boot-support volume, not the Linux root. |
| Stub System `restore` | Absolute symlink to `/Volumes/Preboot/<VG>/restore`. This records the current installation's mount assumption. |
| Stub Data | Stage-1 raw image, its pre-watchdog-fix backup, installation notes and newer 1TR scripts. Copies also exist under `Users/Shared/`. A disabled historical stage-2 binary remains there. |
| Stub Preboot `<VG>/boot/active` | 96-character selection string matching the live policy's `nsih`. |
| Stub Preboot `<VG>/boot/<nsih>/` | Apple `kernelcache`, `apticket.der`, the custom stage-1 boot object, signed `iBoot.img4`, device tree, root hashes and FUD firmware. |
| Stub Preboot `<VG>/restore/` | J713 BuildManifest, version plists, tickets, restore kernelcache, Bootability framework, board firmware, root hashes and trust caches. |
| Stub Preboot `<VG>/restore.failed.1788252706/` | Retained historical repair backup. Its current contents are not necessarily an untouched image of the original failure. |
| Stub Preboot `<VG>/var/db/` | `AdminUserRecoveryInfo.plist`; inventoried by size/hash without copying its contents. |
| Stub Recovery `<VG>/boot/<nsih>/` | Paired Apple recovery boot files, tickets and firmware. No custom stage-1 object was found in this recovery boot directory. |
| Stub Recovery `<VG>/usr/standalone/firmware/arm64eBaseSystem.dmg` | Recovery image, 1,562,348,679 logical bytes. The selected recovery boot directory links to it. APFS physical consumption is lower than its logical size. |
| Shared iSCPreboot `<VG>/LocalPolicy/` | Policy `.img4`, `.recovery.img4`, and `.fuos.im4m` files named with the live `lpnh`. Inventoried by hash without copying their contents. |
| ESP `m1n1/boot.bin` | Installed all-in-one cleanroom stage-2 / DTB / U-Boot / GRUB / kernel / initramfs bundle. |

Both selected Apple boot trees contain firmware including ANE, AOP, AVE, SecurePageTableMonitor, TrustedExecutionMonitor, DCP, GFX, ISP, InputDevice, MtpFirmware, Multitouch, PMP, SIO, iBootData and trust-cache objects. Their presence does not establish Linux driver support for every device.

The ESP has no `EFI/BOOT` or `EFI/fedora` directory. It also retains `.CAP` diagnostic files, `ubootefi.var`, Spotlight and trash metadata. Inventorying these does not make them required installation inputs. The tested U-Boot configuration uses `CONFIG_ENV_IS_NOWHERE=y`.

## Verified stage-1 selection and chainload target

`bputil -d -j -v <VG>` reported `properly_paired=true`, `security_mode=permissive`, `coih_exists=true`, `sip0=0`, and `sip2=false`. Its `os_paired_to_current=false` field is recorded separately from `properly_paired`; this audit was performed in the main macOS installation.

`boot/active` equals the policy's `nsih`. Under that selected directory, the file `System/Library/Caches/com.apple.kernelcaches/kernelcache.custom.<coih>` exists and is 1,198,845 bytes:

`sha256:93ea9886aff203dce8324151204d319241e1fd7803318f03ce240eea76ef7306`

The complete staged raw stage-1 image occurs byte-for-byte at offset 74 inside that custom object. This is an opaque byte comparison, not firmware disassembly or a new cryptographic signature verification. Raw image size is 1,196,032 bytes, divisible by 16,384:

`sha256:216ebba80fde33cf736f33fffabd110a0669f45b9878c07e584097da1148ffa0`

Its appended variables select:

```text
chosen.asahi,efi-system-partition=4BFE423A-75F2-4D77-9F53-28523848C31B
chainload=4BFE423A-75F2-4D77-9F53-28523848C31B;m1n1/boot.bin
```

The pre-watchdog-fix raw backup and the old installation note instead identify `sha256:f34faae4f44dd4e9cd412d1b0b1b4ccee5e2328d6697f9ca58ef88eee5da90c1`. That older complete raw image does not occur inside the selected custom object. The note is stale about the exact installed bytes.

The current ESP `m1n1/boot.bin` is 59,173,973 bytes with `sha256:d6de062082353957042246a8dcd47c404ddccdb974722c4cd3918585fec0756f`, exactly matching the cleanroom reference bundle and prior Fedora disk-boot evidence.

## Restore provenance and historical script differences

The active stub BuildManifest contains exactly one identity: board `0x2C`, chip `0x8132`, device class `j713ap`, `Erase`, `macOS Customer`, version 26.6.2 / build 25G83. It matches the local exact-J713 IPSW extraction output.

The local `extract-m4-restore.py` selects that identity and extracts its referenced `Firmware/` objects. It does not perform the additional container/plist conversion into Linux touchpad firmware or Broadcom firmware naming.

Correction after the owner's firmware question: the handoff's description of `tpmtfw-j713.bin` as personalized to this installation was too broad. Using the standard Asahi multitouch converter's pure plist-conversion functions, the extracted IPSW `Firmware/J713_Multitouch.im4p` produces an 80,524-byte file exactly matching the known-good initramfs file, SHA-256 `9fcbef0cff6a3b7733a4df626c9eba859b66c8e5053557924b8020a67ef48b17`. No serial, ECID, target access or calibration was supplied. This demonstrates that this touchpad firmware is reproducible from the selected model/build firmware; it is not unique to this individual Mac.

The conversion source and result are retained in the audit's `firmware-check/` directory. Source: [Asahi multitouch firmware conversion](https://github.com/AsahiLinux/asahi-installer/blob/main/asahi_firmware/multitouch.py), captured source SHA-256 `00615fc50a822b8a194e37f9a6ba8614f44fdfc5260105fbb043b06a3d2c2700`.

Keep firmware payloads distinct from Apple boot tickets/LocalPolicy and per-device calibration. The accepted m1n1 `src/kboot.c` transfers `wifi-calibration-msf` from the target's live ADT into `brcm,cal-blob`; this is separate from the downloaded Wi-Fi firmware files. The exact firmware build validated here is 26.6.2 / 25G83. Other builds need compatibility validation with the custom kernel and boot stack.

The historical `repair-m4-paired-recovery.sh` copied the main macOS paired restore tree, retained the J713 manifest and stub version, and overlaid only missing board firmware. Inspection compared all 40 current restore regular files against the current main macOS paired restore tree:

- 34 have identical hashes.
- BuildManifest and SystemVersion intentionally differ.
- The current source tree lacks three files present in the stub: `Firmware/all_flash/applelogo@2x~mac-USBc.im4p`, `Firmware/dcp/t8132dcp_restore.im4p`, and `Firmware/pmc/t8132pmcfw.im4p`. Those match the retained overlay.
- The ECID-qualified `apticket.j713ap.*.im4m` differs. Do not transplant personalized tickets between installations.

39 of those 40 current files also match the retained `restore.failed.*` directory; only the ECID-qualified ticket differs. The directory name alone is therefore insufficient to reconstruct the original authentication mismatch. Historical scripts and handoff evidence remain necessary context.

The System volume's 2,378-byte `RUN-ME-IN-1TR.sh` orders `bputil` before `csrutil`. The Data volume and `Users/Shared` copies are 2,865 bytes and match the newer local `finish-m1n1-1tr.sh`, which reverses that ordering and asserts `sip2=true` before `kmutil`. The current policy reports `sip2=false`; this observation does not by itself establish what the field was at the script's pre-kmutil checkpoint. Preserve both versions as evidence rather than treating either as a complete, reusable current recipe.

The initial APFS/GPT creation sequence was not reconstructed as a complete proven transcript in this audit. The four-volume result is verified; copying the retained repair and 1TR scripts is not equivalent to implementing fresh-install preparation.

## Local build and installation inputs

Source workspace: `/home/cleanroom/cleanroom`. The private inventory hashes 70 reference files and links matching hashes to observed on-device files.

| Reference | Purpose |
| --- | --- |
| `tmp/m4-install/M1N1-INSTALLATION.txt` | Historical IDs and artifact note; old stage-1 hash needs the distinction above. |
| `tmp/m4-install/extract-m4-restore.py` and `ipsw/` | Exact J713 restore identity and opaque signed firmware inputs. |
| `tmp/m4-install/repair-m4-paired-recovery.sh` | Historical paired-restore repair, not a fresh-install step to repeat. |
| `tmp/m4-install/finish-m1n1-1tr.sh` | Historical owner-facing custom boot installation. |
| `.work/refactor/install-diskboot-history.sh` | Target-bound one-off ESP installation and read-back verification. |
| `.work/refactor/IMAGE-j713-history.bin` | Exact bundle currently installed on the ESP. |
| `.work/refactor/FEDORA-history.efi` | Standalone GRUB with embedded config, Linux and initramfs. |
| `.work/j713-fedora-coreanalytics-grub.cfg` | Reference GRUB config and bring-up command line. |
| `.work/j713-fedora-init` and `.work/j713-fedora-astra-initramfs.cpio` | Root-mount init and known-good archive; root UUID is installation-specific. |
| `.work/j713-fedora-initramfs-root/` | Static AArch64 BusyBox and nine early firmware/regulatory inputs. The J713 touchpad firmware was reproduced exactly from the selected IPSW, as described above. |
| `tmp/linux-alpha-thermals-build/` | Working kernel config, Image, J713 DTB, `7.1.9-j713-history+` release and thermal module. Full configured module-tree completeness is not established. |
| `m1n1-alpha/build/m1n1.bin` | Current raw m1n1 build output; this shared output name must be separated into stage-specific artifacts during future packaging. |
| `grub-alpha/build-arm64-efi/{config.status,grub-mkstandalone}` | GRUB configure invocation and standalone EFI packaging tool. |
| `u-boot-alpha/build/apple_j713_noenv/` | Tested U-Boot config and binary. |
| `linux-enablement-mac-alpha/build_chainload.sh` | Dynamic EFI padding/header/DT bundle builder; requires `--disk-boot` for this layout. |
| `linux-enablement-mac-alpha/config/apple-pmp-thermal.conf` | Load the matching thermal policy module in the installed root. |
| `.work/refactor/esp-before-diskboot-20260905.tar` | Historical ESP backup, not a generic installation payload. |

## Evidence and inspection limits

Private local evidence directory:

`/home/council/omarchy/mac-m4-installation-audit-20260905.cEUaqf/`

- `files.json` and `files.tsv`: all 680 enumerated entries (536 regular files, 142 directories, two symlinks) beneath the six recorded roots. All regular files have SHA-256 hashes; none changed size/mtime during its read.
- `boot-files.md`: readable file inventory with filesystem service metadata filtered out.
- `permissions-acls-xattrs.txt`: recursive macOS mode/ACL/extended-attribute name-and-size listing. The Python build lacks `os.listxattr`; this separate native listing supplies that metadata.
- `boot-verification.json`: exact active selection, custom-object comparison, restore comparison and snapshot results.
- `reference-inputs.json`: local input hashes and matches to the target inventory.
- `script-references/`: captured installation scripts/configuration as non-executable text references.
- `*.plist`, `gpt.txt`, `stub-policy.txt`, `btrfs-superblock.json`: original read-only observations.
- `mounts-and-gpt-after.txt`: final mount state and GPT comparison.

Preboot, paired Recovery and ESP were initially unmounted, mounted read-only for inspection, then unmounted normally. Existing System/Data mounts were retained. The final GPT text matches the initial GPT text. No installer, policy-changing command, reboot, firmware upload, filesystem repair, or raw write was executed.

This is a file inventory, not an APFS backup or proof of cryptographic authenticity. No complete macOS user filesystem, shared system recovery file tree or Linux Btrfs file tree was copied/enumerated. LocalPolicy and Apple firmware were hashed as opaque data. No fresh installation, newest-stage-1 qualification, untethered boot, or current Linux runtime test is claimed.

`bless --info --mount /Volumes/m1n1` returned that its info option is supported only for external devices on Apple Silicon. The recorded policy and direct Preboot inspection provide the available selection evidence instead.
