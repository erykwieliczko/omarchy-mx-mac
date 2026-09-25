---
title: Install on a Mac
description: Download the signed installer, verify it, and install Omarchy next to macOS.
section: Using it
---

Installation starts in macOS. The installer app downloads the current signed Omarchy release for the channel you pick, resizes the APFS container, writes the Omarchy image and hands over to a first boot that finishes the setup on the Mac itself.

## Before you begin

- Back up macOS and anything you care about. The installer shrinks your macOS volume.
- Check your model on the [Hardware support]({{page:hardware}}) page and the [Asahi Linux device list](https://asahilinux.org/fedora/#device-support).
- The release carries firmware for macOS 13.5 and 14.8.3, and the installer picks one for the Omarchy volume. A Mac running a newer macOS than the release knows about cannot install until the release is updated.
- Keep at least 50 GB free on the internal SSD. 100 GB is comfortable.
- Plug in power and use a reliable Internet connection. The image is a multi-gigabyte download.
- Expect model-specific limits around external displays, speakers, cameras and power management.

## Download

The link never changes and always serves the current installer:

<a class="button" href="https://downloads.aicodelabs.com.au/installer/stable/Omarchy-MX-Mac-Installer.pkg">Download Omarchy MX Mac Installer</a>

Verify the download before opening it. Both commands must report an Apple Developer ID for `MARCELO DE BARROS ALCANTARA (T2C384FJBD)`:

```bash
pkgutil --check-signature ~/Downloads/"Omarchy-MX-Mac-Installer.pkg"
spctl -a -vv -t install ~/Downloads/"Omarchy-MX-Mac-Installer.pkg"
```

<div class="note warn" markdown="1">
Installers older than 2.0.0 were pinned to a single Omarchy release and stop working on 2026-12-01. Replace them with the download above.
</div>

## Run the installer

1. Open the `.pkg`. It installs **Omarchy MX Mac Installer** into `/Applications` together with a privileged helper that performs the disk work.
2. Open the app. It fetches the signed catalog for the selected channel and checks the catalog signature, the sequence number and the SHA-256 of every file it downloads.
3. The app opens on **Stable**. Both channels write the same Aurora image today; choose **RC** in the **Release Channel** menu only if the Mac should follow the release-candidate kernel pin. The choice lasts until the app quits. See [Channels and updates]({{page:channels}}).
4. Choose how much space to give Omarchy. The APFS container is shrunk and three partitions are created: an EFI system partition, a boot partition and a root partition that grows into the free space.
   While this screen is up the app downloads the image in the background, over Wi-Fi or Ethernet only, and **Install** stays disabled until the download matches the signed catalog. Installers after 2.0.10 retry a dropped connection or a server error on their own. If the download still fails, the strip under the disk split names the check that failed (for example not enough free space, or a file that does not match the signed release) and offers **Try again**. Older installers only say that the installation files could not be verified; quit and reopen the app to start the download again.
5. Choose whether to encrypt the root file system. Encryption is set up on the first Linux boot and asks for a passphrase on every boot afterwards.
6. Follow the prompt to complete the boot policy step in recoveryOS. This is Apple's own step and requires your macOS password.

The Mac reboots into Omarchy. The [first boot]({{page:install-flow}}) installs vendor firmware, converts the root to LUKS if you asked for it, creates your user and lands on the desktop.

## Switch back and forth

Hold the power button at startup to pick macOS or Omarchy. In macOS, System Settings → General → Startup Disk selects the default. On the Omarchy side, `asahi-bless` does the same.

## Verify the app instead of the package

If you unpack the app yourself:

```bash
codesign --verify --deep --strict ~/Downloads/"Omarchy MX Mac Installer.app"
spctl -a -vv -t execute ~/Downloads/"Omarchy MX Mac Installer.app"
```

Gatekeeper must report `Notarized Developer ID`.

## Standalone app and advanced options

New source builds also run directly as an unpacked `.app`, including from the
Desktop. They request macOS administrator approval for a temporary installation
service when an operation needs it. The optional package wrapper only copies the
app into Applications. The currently published download above may still use the
older persistent helper; an administrator must retire that helper only after
confirming no operation is active before switching to a standalone build.

In standalone builds, open **Advanced → Uninstall Omarchy…** to remove Omarchy and
its data and return the space to macOS. Review the capacity, type the exact phrase
shown, and enter your macOS administrator credentials. Your macOS files and Apple
Recovery are retained. Keep the Mac powered on until removal finishes. An
unfamiliar or incomplete disk layout is refused; interrupted removal requires
review before further disk changes.

Locally built ad-hoc apps do not require a paid developer account to start their
helper. This does not grant downloaded apps notarized Gatekeeper approval.

Development builds recognize MacBook Neo automatically and show “MacBook Neo”
in the hardware header. Selecting Neo manually
in Advanced uses the same experimental path: pinned Apple 26.6.2 Recovery and
firmware, the original Linux userspace payload, and a third Aurora Silicon download
from Backblaze. Its m1n1 and U-Boot live separately on EFI under
`aurora/boot/boot.bin`; the original engine and OS archives remain unchanged.
The bundle's size and SHA-256 are pinned in the app and verified before disk
changes. The third archive supplies the J700 kernel, modules and initramfs under
`/aurora`, with **aurora-silicon-dirtyroom-J700** as the default boot entry and the
stock kernel retained as a fallback. Boot updates and encryption re-keying rebuild
the Aurora image with the current root arguments. This is an experimental kernel
candidate, not hardware qualification; onboard Bluetooth and graphics/userspace
compatibility still need testing. M1/M2 continue to use their existing kernel.
Initial checks ask you to update
macOS to 26.6.2 or later if the host system firmware is too old for this Recovery
version. Complete the update in System Settings → General → Software Update,
restart, then reopen the installer. M1/M2 selections keep their existing Apple
firmware paths.

The encryption checkbox is passed to first boot in `omarchy/install.conf` on the
Omarchy EFI partition. An unchecked box writes `encrypt=0`. The corrected
standalone installer verifies the actual EFI mount before saving this choice,
including when the installation engine has already mounted the volume. If saving
fails, the Recovery screen warns **Encryption choice not recorded: first boot
will encrypt**; the absent-file default is encryption enabled.
