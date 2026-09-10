# Omarchy MX Mac 2.0.0 private qualification

This candidate targets M4 MacBook Air (`apple,j713`) and MacBook Neo
(`apple,j700`) using one kernel, initramfs, and stage-two boot bundle. Neo's
macOS-side installation completed on physical hardware. Recovery authorization
and Linux boot have **not** been qualified for this candidate. M4's configuration
and device tree remain included; this candidate has not been physically booted
on M4.

This record does not change the public release authorization or physical
allowlist. The owner separately authorized this private build, deployment,
publication, and Neo installation. Normal `apple,j614s` rejection remains intact.
Developer override remains a separate best-effort path.

## Immutable sources

| Component | Repository | Commit |
| --- | --- | --- |
| Installer | `erykwieliczko/omarchy-mx-mac` | `36f8e190` |
| Image builder | `erykwieliczko/omarchy-iso` | `b20c8b90ebcf6c7d8cbae4e89bbe8591d9d48dfb` |
| Linux | `aurora-silicon/cleanroom-linux` | `d31ff64b6332333f770d8d15642198ed20489403` |
| m1n1 | `aurora-silicon/m1n1-cleanroom` | `1ab1683c8ef21b8e6f74253782a9c5af44220eee` |
| U-Boot | `aurora-silicon/u-boot-cleanroom` | `ab2cdaf1dd94dc75d43935ec7c79bd86bfcfc167` |
| Neo policy converter origin | `linux-enablement-mac-alpha` | `eb18361654bef156f170f1a70530227757056933` |
| GRUB | `grub-alpha` | `d38d6a1a9b79427848976f53d474392cd29c2a71` |

The kernel release is `7.1.9-omarchy-mac.1`, with 1,564 matching modules.
The shared configuration retains M4 requirements and adds Neo support. In
particular, `NR_CPUS=64` preserves M4's CPU capacity; the smaller Neo-only
configuration is not substituted for the M4 base.

The J713 device tree comes from the selected kernel. J700 uses the selected
U-Boot repository's first-light fixture through the image builder's
`build_neo_dtb.py`. Both device trees are included in the same `boot.bin`.
Neo-specific kernel command-line options remain limited to Neo's EFI variant.

## Artifact identities

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Shared kernel `Image` | 47,303,168 | `10be345466530a4230a39cab0a4eb798e37f224fb84a83aa34ac6d315ae665db` |
| Shared `initramfs.img` | 36,128,280 | `53d6e12f5d6fc98de10471d37f313d2fcce9bc33a139b136c75f245546d2c676` |
| Shared `boot.bin` | 1,506,572 | `85fade16bcd882ae5f047e875cce8861e2f582a60de86e893ed4082742c9a138` |
| OS ZIP | 3,755,726,609 | `45c86aec35ce246732c7a10f98d1b6936232fda41876a290627136f71f3a4f11` |
| HTTPS installer package | 25,348,869 | `1bffc55a73d4fb1468ec22d1c9260b4017f62bb1c1090cc2c9a56886d8c0740f` |

The package is named `Omarchy-MX-Mac-2.0.0.pkg`. It embeds the inspection engine
and signed catalog, not the OS ZIP. The two model entries reference the same OS
archive. The archive has separate `esp-j713` and `esp-j700` variants containing
the identical shared kernel, initramfs, and `boot.bin`.

The final engine is `v2.0.0-cleanroom.3`, with an 8 GiB execution scratch reserve.
The physical test used `v2.0.0-cleanroom.3-dev`, with a 20 GiB reserve and the
development Apple cache enabled. The runtime source is the same; the test
catalog used an owner-authorized LAN origin. The final package uses HTTPS
Backblaze artifact URLs and has no development cache marker.

## Firmware boundary

The distributed filesystem was repacked into a fresh filesystem after removal
of vendor firmware and firmware packages. Firmware-free image verification
passed. Neither the OS archive nor the packaged engine includes Apple recovery
images, firmware originals, converted firmware, or target calibration files.

Both profiles retain authenticated Apple download descriptions. Neo's Linux
firmware baseline is build `25G83`; the physical host's compatible Recovery
selection was `25F84`. These are selected independently.

On physical Neo, the range extractor fetched nine Wi-Fi originals from Apple
using 3,213,393 bytes. The standalone native qualification verified 249 generic
outputs and two calibration files obtained from that target. Memoizing parsed
Apple Wi-Fi tables reduced conversion from 518.89 seconds to 12.65 seconds,
without changing any of the 249 pinned output hashes.

The installer run repeated the authenticated range fetch in 2.42 seconds and
prepared 251 files. Apple boot inputs were reused from the development cache.
Consequently, this run is not a measurement of a cold Apple Recovery download.

## Automated and packaging checks

- 99 focused engine tests passed.
- 95 overlay transaction tests passed.
- 22 adapter tests passed, including model selection before disk planning.
- Six image-builder tests passed for shared boot and firmware-free verification.
- The native Swift suite passed 273 XCTest cases and four Swift Testing cases
  in both debug and release. The additional dual-model override test then passed
  in both configurations. Strict Swift formatting passed.
- Both native packages passed catalog signature, reciprocal app/helper signing,
  source pin, archive structure, firmware exclusion, and embedded uninstaller
  checks. The private app uses ad-hoc signing; this is not Developer ID or
  notarization evidence.

## Physical Neo result

VNC review confirmed automatic supported-host detection, both override choices
with M4 first, the uninstall button, and the skip-`boot.bin` option and help.
The installation used native Neo detection with both developer options off.

The macOS-side engine ran from `2026-09-10T18:00:04Z` to
`2026-09-10T18:05:17Z`, exited zero, and reported `awaiting_recovery`.
Authenticated checkpoints recorded both `stub-and-esp-installed` and
`recovery-handoff-prepared`.

The resulting allocation retained approximately 200.2 GB for macOS and assigned
44.9 GB to Omarchy. Its 4.3 GB APFS stub retained approximately 1.9 GiB free after
Recovery preparation. Apple ISC and System Recovery were preserved.

The engine compared the written 2 GiB boot image and 32 GiB root image against
their archive contents and recorded successful hashes. Independent read-back
of the EFI kernel, initramfs, and `boot.bin` matched the artifact identities
above. The installed vendor firmware archive contained 251 files.

The GUI reached the shutdown/startup-options instructions. Finishing requires
holding the physical power button to enter startup options and selecting
Omarchy's finish-installation flow. No claim of Recovery authorization, Linux
boot, Wi-Fi operation, or desktop operation follows from this stage-one result.

Full device journals and VNC screenshots are retained in the private build
workspace. This repository record omits machine identifiers, credentials,
calibration contents, and reusable authorization material.
