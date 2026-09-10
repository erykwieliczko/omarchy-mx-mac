# Omarchy MX Mac 2.0.1 private qualification

This candidate incorporates the cleanroom disk-boot handoff for MacBook Neo
(`apple,j700`) while retaining the shared M4 MacBook Air (`apple,j713`) kernel,
initramfs and boot bundle. The current kernel's country requirement remains;
first-run setup now obtains and persists the user's actual country before
opening network selection.

The owner authorized this private build, publication, deployment and Neo
reinstallation. Public release authorization and the physical allowlist remain
unchanged; normal `apple,j614s` rejection remains intact.

## Immutable sources and changes

| Component | Commit |
| --- | --- |
| Installer | `733b73acf4d431a9a8f75a68dd9e920b612f65bd` |
| Image builder | `211c4722c8eb8e9a6b1f7a5cfee32449a4edcfbc` |
| Linux | `d31ff64b6332333f770d8d15642198ed20489403` |
| m1n1 | `1ab1683c8ef21b8e6f74253782a9c5af44220eee` |
| U-Boot | `eb052fe3f2871bf8cdddf25ec600ef7dfec0269f` |

U-Boot retains inherited M4 watchdog handling and supported USB operation. Its
Neo guards and regenerated device tree include Linux NVMe resources and the
selective Wi-Fi SID16/18 bypass with the paired live-ADT qualification marker.
The disk-backed EFI command is `bootefi ${loadaddr} ${fdtcontroladdr}`, retaining
UUID-bound ESP selection and the bounded file load. Both model command lines
use `quiet loglevel=3`; Neo idle workarounds remain.

The kernel release remains `7.1.9-omarchy-mac.1`, with 1,564 matching modules.
No replacement kernel, m1n1 or diagnostic initramfs is included. The first-run
provisioner merges the selected ISO country into iwd and wireless-regdom
configuration, preserves unrelated configuration, and offers an explicit
Ethernet/offline path if no suitable country policy is available.

## Artifact identities

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Shared kernel Image | 47,303,168 | `10be345466530a4230a39cab0a4eb798e37f224fb84a83aa34ac6d315ae665db` |
| Shared initramfs | 36,128,280 | `53d6e12f5d6fc98de10471d37f313d2fcce9bc33a139b136c75f245546d2c676` |
| Shared boot.bin | 1,508,304 | `49911e9140d021df496740c6d37109c0ffe91be9a5ad8d425cab51f96e9807c3` |
| OS ZIP | 3,750,351,456 | `8bffe799244c6b868c0c6150f615903ce1224a948484ea5c4a7d38de1c56cfbb` |
| HTTPS installer package | 25,348,643 | `89112ceca9de82a47e36489dd5ded54677fc7aac1570f2f5179d7aa80a6bf632` |

The normal engine is `v2.0.1-cleanroom.1`. The HTTPS package uses build 203,
Backblaze URLs and an 8 GiB execution reserve. Physical testing uses build 202
with the same runtime source, a LAN catalog, explicit development Apple cache,
and a 20 GiB execution reserve. Neither package embeds the OS archive.

## Validation

- Engine 99, transaction 95, adapter 22 and shared-boot five tests passed.
- U-Boot watchdog, compiled NVMe and compiled Wi-Fi device-tree tests passed.
- Country persistence and setup-form focused shell tests passed.
- Native debug and release each passed 274 XCTest and four Swift Testing cases;
  strict Swift formatting passed.
- Both packages passed structure, signed catalog, reciprocal app/helper signing,
  source pins, firmware exclusion and embedded uninstaller checks. Signing is
  private ad-hoc signing, not Developer ID or notarization evidence.
- Image verification passed kernel/module consistency, installed provisioner
  and country-helper identity, firmware exclusion and diagnostic exclusion.

Apple boot and firmware inputs are obtained at installation time. Distributed
artifacts do not contain Apple Recovery images, Apple firmware originals,
converted Apple firmware or target calibration files. The Neo run fetched nine
Wi-Fi originals from Apple in 3,213,393 bytes and 1.85 seconds, then prepared
251 Linux firmware files including this target's calibration. Recovery inputs
used the development cache, so this is not a cold-download measurement.

## Physical qualification

The packaged uninstaller removed the previous Neo installation and expanded
macOS while preserving Apple ISC and System Recovery. VNC inspection confirmed
native Neo detection and installation with the developer options disabled.
The new macOS-side engine ran from `2026-09-10T22:41:52Z` to
`2026-09-10T22:47:10Z`, exited zero and reported `awaiting_recovery`.
Both installed-content and Recovery-handoff checkpoints completed.

The resulting layout retains approximately 200.1 GB for macOS and allocates
44.97 GB to Omarchy, including a 4.3 GB APFS stub. The prepared stub container
retains about 1.9 GiB free. The installer verified the written 2 GiB boot image
and 32 GiB root image against their archive contents. Independent read-back
of the ESP kernel, initramfs, GRUB and corrected boot.bin matches this candidate.
The installed vendor archive contains 251 files.

The GUI reached the final Recovery instructions. Its Shutdown button and
confirmation were exercised through VNC; the VNC connection closed and a
subsequent SSH connection timed out. The temporary LAN server was stopped.
Finishing requires holding the physical power button, selecting Omarchy and
completing its Recovery authorization.

Cable-free Linux boot, normal reboot, persistent country and Wi-Fi operation,
quiet first-run UI, and M4 hardware retest remain unqualified for this candidate.

## Distribution

All artifacts are uploaded under the unique Backblaze prefix
`omarchymacexperimental/20260910T220435Z/FILES`. HTTP HEAD returned 200 and the
expected Content-Length for the package, engine, metadata, signed catalog and
OS ZIP. No checksum readback was performed. The HTTPS package is on the test
Mac's Desktop; the physical run used the separately named LAN test package.

Device journals, receipts and screenshots are retained in the private build
workspace. This record excludes credentials, calibration contents, machine
identifiers and reusable authorization material.
