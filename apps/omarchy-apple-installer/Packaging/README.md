# Apple installer packaging

`build-app.sh` assembles a signed macOS application bundle without installing it,
registering its privileged helper, submitting it for notarization, or changing a
disk. The result is safe to inspect before any separately authorized deployment
step.

## Bundle layout

The generated `Omarchy MX Mac Installer.app` contains:

- the SwiftUI application in `Contents/MacOS`;
- the root helper in `Contents/Resources`;
- its `SMAppService` launch-daemon property list in
  `Contents/Library/LaunchDaemons`;
- the immutable release descriptor and Ed25519 trust root in
  `Contents/Resources/Release`; and
- the pinned Asahi validation engine in `Contents/Resources/Engine/artifacts`.

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
for local structural validation only. A named identity also requires its
10-character team identifier:

```sh
OMARCHY_APP_SIGNING_IDENTITY="Apple Development: Name (TEAMID)" \
OMARCHY_TEAM_ID="TEAMID" \
Packaging/build-app.sh /absolute/path/to/release-inputs /absolute/path/to/output
```

Production distribution must use a `Developer ID Application` identity, a
hardened-runtime signature and secure timestamp, followed by notarization and
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

## Disk image

After building the signed installer package with `pkg/build-pkg.sh`, wrap that
package in a compressed disk image on macOS:

```sh
bash Packaging/build-dmg.sh \
  /absolute/path/to/Omarchy-MX-Mac-Installer.pkg \
  /absolute/path/to/Omarchy-MX-Mac-Installer.dmg
```

The image contains the original package and brief installation instructions.
The package installs the app and its system helper; copying an app alone would
not install that helper. The wrapper verifies the staged package signature and
the completed image checksum, refuses to overwrite an existing output, and
prints the image's SHA-256. It neither installs nor executes the package.

This step does not build the platform payload, change model eligibility, sign
the disk image, or submit it for notarization. Release qualification and any
required signing, notarization, or publication remain separate steps. A DMG
made from a test or historical package retains that package's capabilities.

### Private J713 testing package

The private cleanroom candidate supports MacBook Air M4 J713 / Mac16,12 on
macOS 26.6.2 (25G83). It includes the OS payload and Apple restore subset and
requires an internet connection for Apple's public AEA release key. Its first
Linux boot is a console: the accepted kernel does not enable the Asahi GPU.

Build the cleanroom engine and sealed release with `Engine/cleanroom/` and the
ISO repository's `builder/cleanroom/` image recipes. The source/artifact locks,
root verification receipt and payload receipt belong to the same candidate.
Then, on macOS:

```bash
OMARCHY_PRIVATE_PACKAGE=1 OMARCHY_BUNDLED_RELEASE=1 \
  Packaging/build-app.sh /path/to/Release /path/to/new-app-output
Packaging/pkg/build-pkg.sh --private-unsigned \
  --app '/path/to/new-app-output/Omarchy MX Mac Installer.app' \
  --version 0.7.0 --out /path/to/new-private.pkg
Packaging/build-dmg.sh --private-unsigned /path/to/new-private.pkg /path/to/new-private.dmg
```

These flags produce an unsigned, unnotarized package for direct private testing;
they never publish a catalog, register a helper, alter user trust settings or
run an installation. Package installation is a separate owner action.

The private build pins the helper's exact executable hash in the app. Its
embedded daemon admits no client. After app signing, package assembly derives a
root-owned system daemon with the exact app executable hash. It verifies both
requirements against the staged files. A same-identifier different executable
cannot use those requirements. The helper independently loads its root-owned
sealed catalog and validates its signature, expiry, model and artifact identities
on every submission. Installation-capable named-identity builds also require a
sealed catalog; the ad-hoc inspection-only build remains available.

Bundled asset filenames must match their signed HTTPS URL basenames. Every
included byte is checked against the catalog; private artifact URLs are names
for embedded assets and are not publication endpoints. Physical install and
paired Recovery boot remain owner smoke tests, separate from build evidence.

### M4 download distribution

The normal graphical installer downloads its OS through the existing signed
catalog and verified HTTPS stager. For M4, keep only the authenticated cleanroom
inspection engine in the app:

```bash
python3 Engine/cleanroom/build_release.py /path/to/engine /path/to/payload.zip \
  /path/to/new-Release --artifact-base-url https://downloads.example.org/immutable-release
OMARCHY_PRIVATE_PACKAGE=1 OMARCHY_ENGINE_ONLY_RELEASE=1 \
  Packaging/build-app.sh /path/to/new-Release /path/to/new-app-output
```

The example host must be replaced by the actual artifact location. Serve the
exact engine archive, metadata and payload named by the generated catalog.
Release preparation does not upload anything. `Assets/` contains only the
inspection engine; including an OS there makes engine-only verification fail.
Use the existing small PKG helper installation when distributing the graphical
app. The previous private DMG and offline bundle are superseded for normal
M4 distribution; a PKG is already part of the repository's helper design.

The payload still needs temporary download space. The current staging cache is
retained for retries; successful installation does not yet purge that cache.

### Engine diagnostics

Inspection and planning retain diagnostics under
`~/Library/Application Support/com.omarchy.mx.installer/scratch/diagnostics/`.
Privileged execution uses `/var/db/com.omarchy.mx.installer/diagnostics/`.
Each run has a unique directory with operation/start/exit metadata, stdout and
stderr, the available engine log and journal, and the engine version. Each
output log retains at most its final 1 MiB; the authoritative execution journal
remains separate and unchanged. Directories are mode 0700 and files 0600.
The engine's supplied password is redacted across output chunk boundaries;
stdin and environment variables are never recorded by the diagnostic collector.
Logs can contain machine identifiers and should be reviewed before sharing.

A failed engine exit reports the operation, exit code, last error output and
exact log directory. These details also survive the helper's XPC error bridge.
The installer does not claim that an engine failure occurred before disk work.

### Local HTTP smoke tests

An owner-authorized private build may use a single local HTTP origin. Generate
its catalog with `build_release.py --private-http --artifact-base-url
http://LOCAL_IPV4:PORT/release-path`, and set `OMARCHY_PRIVATE_HTTP_ORIGIN` to
`http://LOCAL_IPV4:PORT` when running `build-app.sh` with the private and
engine-only flags. Packaging admits only explicit LAN, loopback or tailnet IPv4
addresses, seals that origin and an exact ATS host exception into Info.plist,
and compiles the private transport flag into both app and helper. Public builds
continue to reject HTTP. File sizes, hashes, catalog signatures, helper
requirements and device admission are unchanged. HTTPS requests cannot redirect
to HTTP; private HTTP requests cannot redirect to another origin.

Serve only the prepared release files from a dedicated directory. This mode is
for temporary private testing, not public distribution.
