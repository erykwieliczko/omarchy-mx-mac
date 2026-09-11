# Omarchy MX Mac 2.0.3 private qualification

This candidate integrates the published Neo world-domain Wi-Fi handoff and
separately enables Tailscale's missing kernel networking requirements. M4 and
Neo retain the same kernel and matching module/initramfs build.

## Inputs and changes

- Runtime/installer source: `42aa553b` (country change `9e3f0ffe`).
- ISO image recipe: `edaa20e`.
- Linux: `6ba27a80f991f1627f41fa71aa241d600246e948`, published cleanroom branch.
- Firmware tooling: `6b4eb9858dc5841e3a848cd21b727ab32d7f44e6`, published main.
  Vendored files match that revision except the existing package-relative
  imports and read-only parsed-table memoization.
- Kernel release: `7.1.9-omarchy-mac.2`, package `7.1.9.mac-2`, 1,565 modules.
- m1n1, U-Boot, GRUB and both device trees are unchanged from qualified 2.0.2.
  The freshly computed combined boot.bin matches the prior qualified artifact.
  Both ESP variants receive the new exact Image and initramfs.

Apple originals produce `mediatek/mt7932/policy/world-XZ.bin`, SHA-256
`926b390f709a4125a53ecbc12d5b94eba765f142e099b9a711b43dda23f7aea0`.
Its J7RP identity remains XZ; the generation manifest separately records Linux
country 00. Explicit unavailable countries do not silently fall back to XZ.
The firmware package verifier parses the complete newc archive, resolves the
producer's deduplicated hardlinks, checks truncation/trailing data and compares
all firmware bytes with the installed-tree tar. Target calibration remains
collected from the installation's own Mac.

First-run setup no longer requires a country selection for discovery. An unset
country does not write either persistent override; intentional existing country
settings are preserved. Fresh media rejects inherited country overrides.

The kernel adds IPv4/IPv6 multiple routing tables, TUN and connmark requirements.
Builds explicitly pass LOCALVERSION=, verify the compiled release and Image
banner, check every module's vermagic and reject extra module-release trees.
Every initramfs module must match the admitted release and exact bytes.
Diagnostic supplements/test Images are rejected; the default boot chain uses
the actual newly created ESP UUID, never the handoff machine's previous UUID.

## Validation

- 99 engine tests and 23 staged adapter tests passed.
- 11 kernel requirement tests and 18 image-builder tests passed.
- Country-setting tests passed, including no writes/restarts for unset country.
- All 16 published MT7932 host test programs passed.
- The published original-XZ producer test passed against private original inputs
  and independent numeric fixtures. The installer converter produced all 249
  expected Wi-Fi files with exact pinned hashes; full tar/CPIO verification passed.
- Native macOS debug and release each passed 274 XCTest and 4 Swift Testing tests.
- The final read-only image audit passed, including firmware absence, clean
  active/factory runtime dependencies, country defaults and matching modules.
- Both native packages passed catalog, component/source pins, reciprocal app and
  helper signatures, package layout and embedded firmware/OS-image exclusion.

The exact new Image and matching modules booted in an isolated QEMU ARM64 guest
with no external NIC. Two network namespaces ran real Tailscale peers against a
local testcontrol/DERP/STUN service. Tailscale source was
`3d52c3f03e96321f1937e778e99461447b9d88dd`. IPv4/IPv6 fwmark routing and connmark
save/restore rules succeeded. Both iptables and native nftables modes exchanged
direct Tailscale pings and kernel TUN IPv4/IPv6 ICMP traffic with zero packet loss.
The initial harness omitted local DERP and stalled startup; adding the local
service resolved the harness defect without changing the kernel candidate.
The test guest was stopped after successful checks. This is kernel/networking
validation, not a claim of Wi-Fi RF or existing-tailnet qualification.

## Artifacts

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| OS ZIP | 3,752,767,931 | `bc20c8e62981b1541bb91ffe8a1a74bf2bb4cb5c2d8666ad543ebbf2001a9db6` |
| HTTPS package | 25,349,632 | `dffd7c6ae4902c59114b5f579b56ef3334db4e2872591d61ab5111cad8a586f6` |

Engine version is `v2.0.3-cleanroom.1`. LAN build 208 explicitly enables Apple
input caching and uses a 20 GiB execution reserve; HTTPS build 209 has no dev
cache marker and uses the normal 8 GiB reserve. Neither package embeds the OS
ZIP or Apple firmware. The distribution prefix is
`https://f005.backblazeb2.com/file/omarchymacexperimental/20260911T121354Z/FILES/`.

## Neo installation

The owner explicitly authorized uninstalling and reinstalling Neo after the
new candidate passed validation. Both packages are on its Desktop. The LAN
package was installed, and its Uninstall button removed the old Omarchy
partitions after one password entry, expanded macOS to about 245 GB and
preserved Apple ISC/System Recovery. Native Neo detection is used, with override
and skip-boot options disabled. The development space gate initially rejected
38.2 GiB against its 38.5 GiB minimum; removing only the obsolete 2.0.2 OS ZIP
allowed the normal plan to proceed while preserving the new ZIP and Apple cache.
The default feasible allocation was about 45 GB Omarchy / 200 GB macOS.

Stage one completed at `2026-09-11T12:39:33Z`, exit 0, in 322 seconds, with
`awaiting_recovery`. Independent ESP read-back verified the admitted Image,
initramfs, boot.bin and GRUB hashes. The generated stage 1 exactly matches the
packaged base bound to the newly created ESP; the Recovery script agrees.
The complete firmware tar and CPIO read-back matches all 252 files, including
world-XZ with the expected hash and the target WCAL file. These archives supply
early boot and the installed firmware tree; Linux-side extraction awaits boot.

The GUI completion screen was observed through VNC. Its Shutdown button and
confirmation were clicked after ending the temporary caffeinate process.
Direct-LAN SSH and VNC subsequently became unreachable. The temporary LAN
server was stopped. Private raw diagnostics and screenshots remain in the
local 2.0.3 build workspace, outside the repository.

The HTTPS package upload and HEAD size check passed. The OS ZIP upload remains
in a separate tmux session (about 60% at physical-test completion); the HTTPS
installation is not ready for use until that upload and its HEAD check finish.

Recovery authorization and Linux first-run completion require separate physical
observation. Public release authorization and normal unsupported-model gates
remain unchanged.
