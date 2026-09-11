# Omarchy MX Mac 2.0.2 private qualification

Version 2.0.1 reached Linux setup on Neo, but owner creation stopped at line 753
because `install/helpers/browser-policy.sh` was missing. The image recipe had
updated the provisioner without admitting that helper or its `as-root.sh`
dependency. Inspection of the immutable 2.0.1 filesystem reproduced the missing
files. This was an image-packaging defect, not a kernel failure. The base also had
an older theme writer that could not update the hardened policy directories;
the matching writer and restricted privilege grant must accompany the helper.

The image builder now stages the provisioner, setup form, Wi-Fi country helper,
browser-policy helper, as-root helper, both browser theme commands and their
narrow sudoers rule as one explicit dependency set. The
same mapping drives manifest requirements, installation and verification. Both
the active root and factory-reset snapshot must contain the exact admitted
files. Verification loads the actual browser helper chain without executing its
policy writes. Packaged command symlinks are replaced without following their
absolute targets into the build host.

## Sources and validation

- Installer source: `507d3888`; app and engine source unchanged from 2.0.1.
- Image-builder source: `900dfb5`.
- Shared Linux, m1n1, U-Boot, both device trees and GRUB remain pinned as in
  2.0.1. The shared kernel release remains `7.1.9-omarchy-mac.1`.
- Seventeen image-builder tests passed, including missing direct/nested helpers,
  loading an unstaged nested source, stale provisioner copies and safe symlink
  replacement.
- Existing browser-policy, sudoers, provisioning-group and setup-form tests passed.
- A disposable-container smoke test executed the actual theme setter as an
  unprivileged wheel user against root-owned policy directories. It used the
  narrow sudo grant without prompting and wrote the expected root-owned 0644
  color.json. This closes the old base-image writer compatibility gap.
- Read-only image verification passed for both active and factory runtime sets,
  kernel/module consistency, firmware exclusion and diagnostic exclusion.
- The regenerated initramfs matches 2.0.1 exactly; the qualified boot directory
  is reused unchanged. Kernel compilation and bootloader changes are unnecessary
  for this fix.
- The user finalizer in the base image is byte-identical to the selected runtime
  source. Its sourced setup leaves were checked against the image inventory.

The owner authorized a new private installer and destructive Neo reinstallation.
Normal unsupported-model rejection and public release authorization are
unchanged. No Apple firmware is embedded in the distributed filesystem.

## Artifact identities and package verification

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| OS ZIP | 3,751,246,814 | `98ad9ac014d7b639bd4a250a7c574a309edc96b55cb8b63f7bd1511ee76cb142` |
| HTTPS package | 25,348,623 | `7af57801c5c788a9ea1421c480bffca3588812c3bd715c0bc246f0e817b1e170` |

The engine is `v2.0.2-cleanroom.1`. Native LAN build 206 and HTTPS build 207
passed package layout, signed catalog, reciprocal app/helper signing, immutable
source pins and firmware exclusion. The application and engine source are
unchanged from the 2.0.1 debug/release qualification. The LAN test explicitly
uses cached Apple Recovery inputs and a 20 GiB working reserve; the normal
HTTPS package uses an 8 GiB reserve without that development marker.

Both packages were placed on the test Mac's Desktop. The packaged uninstaller
removed the previous installation and expanded macOS, preserving Apple ISC and
System Recovery. The test installed the LAN package and selected native Neo
support with both developer options disabled. An obsolete cached 2.0.1 OS ZIP
was removed to provide development working space; the Apple cache was retained.

The new run downloaded nine Wi-Fi originals from Apple using 3,213,393 bytes
in 2.37 seconds and prepared 251 Linux firmware files. Boot inputs used the
verified development cache; this does not measure a cold Recovery download.

The macOS stage completed with exit 0 in 311 seconds, from 06:56:25Z to
07:01:36Z on 2026-09-11. Written boot/root image verification passed; independent
readback of boot.bin, Image, initramfs and BOOTAA64.EFI matched the admitted
artifacts. The APFS stub retained about 1.9 GiB free after Recovery preparation.
The default installation allocated about 44.8 GB to Omarchy and 200.2 GB to
macOS. Both stub/ESP and Recovery-handoff checkpoints were recorded.

Diagnostics, journal/checkpoints, final partition layout, boot hashes and the
completed-stage screenshot are retained in the private installer-2.0.2 build
workspace. The installer Shutdown button and its confirmation were used;
VNC stopped responding and direct-LAN SSH became unreachable. The temporary LAN
payload server was stopped afterwards.

Recovery authorization requires the owner's physical power-button action.
Recovery and this image's Linux first-run completion have not yet been observed;
the completed macOS stage and isolated setup checks do not establish those results.

## Distribution

All six artifacts were uploaded under the immutable private test prefix
`https://f005.backblazeb2.com/file/omarchymacexperimental/20260911T062701Z/FILES/`.
The HTTPS package is `Omarchy-MX-Mac-2.0.2.pkg`. Successful HEAD responses and
expected lengths were checked for the package, OS ZIP, engine, installer data,
catalog and signature. No checksum readback was performed, as requested.
This verifies availability, not an end-to-end installation over Backblaze;
the physical stage-one test used the separate LAN package.
