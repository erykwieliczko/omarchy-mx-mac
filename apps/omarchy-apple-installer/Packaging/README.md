# Apple installer packaging

`build-app.sh` assembles a signed macOS application bundle without installing it,
registering its privileged helper, submitting it for notarization, or changing a
disk. The result is safe to inspect before any separately authorized deployment
step.

## Bundle layout

The generated `Omarchy MX Mac Installer.app` contains:

- the SwiftUI application in `Contents/MacOS`;
- the root helper in `Contents/Resources`;
- the legacy launch-daemon template in `Contents/Library/LaunchDaemons`
  (retained for old packaging tools, unused by the standalone runtime);
- the immutable release descriptor and Ed25519 trust root in
  `Contents/Resources/Release`; and
- the pinned Asahi validation engine in `Contents/Resources/Engine/artifacts`.

The app runs standalone, including when ad-hoc signed. A `.pkg` is optional;
its wrapper installs only the app, never a persistent daemon. When installation
or **Advanced → Uninstall Omarchy…** needs privilege, macOS asks for administrator
approval. The app stages a root-owned temporary helper, pins both peers to their
exact code hashes, and admits only the initiating process and user. The helper
exits after its operation/session or app exit, draining active disk work first.
Root-owned journals and the process lock remain for recovery.

For hardware experiments, build with `OMARCHY_DEVELOPMENT=1`. This requires
ad-hoc signing and adds **Advanced → Install anyway on an unsupported Mac**.
Choose a model and apply it to use that model's catalog entry, chip identity and
boot firmware deliberately, even on different hardware. The choice lasts only
for the app session; changing it discards the existing plan. Normal builds reject
override requests. Uninstall always uses the real hardware identity.

The development launcher preserves real disk inventory and OS state, checks the
physical host again in the helper, and leaves the explicitly blocked `apple,j614s`
host blocked. It selects the profile's unique firmware build identity during
preflight, before disk changes. Original downloaded archive hashes and signatures
are still checked, but execution is modified by the development launcher; the
execution journal's `.development.json` sidecar records the real and forced model.
Wrong-model firmware is intentional in this mode and does not imply boot support.

Development builds automatically select the Neo profile on `apple,j700`; explicit
Advanced selections still take precedence. Neo alone downloads the pinned Aurora
Silicon bundle described in [aurora-bundle.md](aurora-bundle.md), including the
J700 kernel and its `/aurora` boot-update hooks. Limine selects
`aurora-silicon-dirtyroom-J700` and keeps the stock kernel entry. M1/M2 keep
their original two downloads. The development resource pin is sealed into the
app's signature; changing it requires rebuilding the app.

The temporary helper stops when its owning app exits or crashes, including
SIGKILL. Owner death cancels active operations and kills their subprocess groups;
it unregisters its launchd service and removes its private staging directory.
Interrupted disk work retains its journals and requires verification on retry.
The owner-exit callback runs without main-actor isolation on its dispatch queue.
An explicit session finish while the app remains alive still drains admitted RPCs.

The helper and application use reciprocal code-signing requirements. The helper
also authenticates each XPC client before accepting a request. A release
descriptor whose helper identity does not match the compiled product is rejected.

## Build

Provide a directory containing the production-owned `release.json` and the exact
32-byte `trust-root.ed25519.pub` named by that descriptor:

```sh
Packaging/build-app.sh /absolute/path/to/release-inputs /absolute/path/to/output
```

For a private or offline build, the same directory may also contain the signed
pair `catalog.json` and `catalog.json.sig`. The packager accepts the pair only
when both are regular, non-symlinked files within the catalog size limits and
the signature is exactly 64 bytes. The app verifies this sealed catalog with
the same bundled Ed25519 trust root; when the pair is absent, it fetches the
configured HTTPS catalog as normal.

Build concurrency defaults to 10 workers so a 14-core Mac retains four cores for
responsiveness. Override it with `OMARCHY_BUILD_JOBS`; the same value is exported
as `CARGO_BUILD_JOBS` for nested Rust builds.

The default signing identity is `-`, which creates an ad-hoc development bundle
that can perform installation after local administrator approval. A named
identity also requires its 10-character team identifier:

```sh
OMARCHY_APP_SIGNING_IDENTITY="Apple Development: Name (TEAMID)" \
OMARCHY_TEAM_ID="TEAMID" \
Packaging/build-app.sh /absolute/path/to/release-inputs /absolute/path/to/output
```

For seamless Gatekeeper approval, public distribution should use a
`Developer ID Application` identity, a hardened-runtime signature and secure
timestamp, followed by notarization and
stapling. After an explicitly authorized production build, notarize it with a
preconfigured keychain profile:

```sh
OMARCHY_NOTARY_PROFILE="omarchy-notary" \
Packaging/notarize-app.sh "/absolute/path/Omarchy MX Mac Installer.app"
```

Notarization is deliberately separate from the assembler. The script rejects
ad-hoc and development-signed bundles, submits a temporary ZIP, staples the
accepted ticket to the app, and validates it with Gatekeeper. It requires the
owner's explicit authorization because it uses production credentials and
changes the application bundle.

The script refuses unsafe or mismatched release inputs and will not overwrite an
existing application bundle.

## Existing package installations

A registered older helper blocks the temporary service. Do not unload a helper
while installation or removal is active. An administrator must first verify the
old operation is finished and retire its `/Library/LaunchDaemons` registration.
The app deliberately does not stop an older helper whose activity it cannot prove.
Ad-hoc signing does not provide notarization or bypass Gatekeeper for downloaded
apps; those are separate from approval to run the helper.

## Automated verification

Dispatch **Source tests** with `apple_installer=true` to run strict Swift lint,
debug and release XCTest suites, and focused engine tests on a full-Xcode macOS
runner. Its virtual hardware cannot satisfy the physical-Mac identity assertion;
only that live inspection test is excluded. Fixture-based unsupported-host and
protected-partition tests still run. Check live inspection and the actual UI on
the authorized physical Mac separately.

## Encryption choice handoff

After the engine completes, the helper writes `omarchy/install.conf` on the
new Omarchy EFI partition: `encrypt=0` opts out, `encrypt=1` enables encryption.
First boot defaults to encryption when the file is absent. A failed handoff
shows “Encryption choice not recorded: first boot will encrypt.”

The engine may leave the EFI volume mounted under `/Volumes`. macOS can report
success for `diskutil mount -mountPoint` while leaving that existing mount in
place. The helper therefore unmounts that volume without force, mounts it at
its private destination, and verifies the device identifier, actual mount path
and writable status before writing. Cleanup after a failed verification removes
only an empty directory, never the contents of a possibly mounted filesystem.

A 2026-09-25 regression check reproduced the old false success on a disposable
FAT32 EFI disk image on mac-m5: readback from the private directory passed while
independent readback from EFI still contained the old value. The corrected writer
passed independent unmount/remount checks for off, on and off again, starting
with the volume already mounted. Feeding the resulting off document to the
first-boot consumer recorded `phase=declined` without accessing the root disk.
This verifies the handoff, not a new physical OS installation.
