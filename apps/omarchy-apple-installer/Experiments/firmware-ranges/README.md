# Apple firmware range prototype

This experiment recovered the six pinned J713 Wi-Fi files from Apple's live
25G83 IPSW using **2,865,066 bytes in 1.916 seconds**, instead of downloading
the **10,334,765,056-byte system image**. All output SHA-256 values match the
existing installer firmware lock. The test ran on the Linux build machine,
with a fresh output directory. `strace` confirmed it never opened the local
AEA, decoded image, baseline firmware, or installer development cache.

Adding `System/Library/CoreServices/SystemVersion.plist` through the input file
list produced seven correct files with 3,639,513 bytes in 2.750 seconds. Those
are measured samples, not guaranteed download or installation times. Byte
counts include HTTP range bodies; the small Apple FCS key response and HTTP/TLS
framing are excluded. Receipts and the metadata-only recipe are in `evidence/`.

**This is a prototype, outside the shipped installer.** It changes neither
0.9.4 nor any Mac. Recovery, boot components, touchpad extraction from IMG4,
and the Omarchy OS download remain separate. The existing selected Recovery
AEA alone is approximately 1.14 GB; this experiment does not remove it.

## How it works

One-time preparation, per exact Apple image:

1. Verify the complete cached AEA and decoded APFS image against the existing
   pinned hashes. Neither is included in the recipe.
2. Read requested files with a read-only APFS parser, recording physical reads.
   Five of the six Wi-Fi files use APFS transparent compression; the preparation
   also collects their compressed attributes and resource forks locally.
3. Locate those exact storage bytes within the recorded physical reads. Record
   their spans, including fragmentation, and map them to AEA segments. Prefer
   already-selected segments and deduplicate shared dependencies. This is a
   bounded greedy optimizer, not a mathematical minimum.
4. Authenticate the complete AEA header chain and retain only the cluster
   headers needed by selected segments. Record encrypted-range hashes and
   expected MACs, storage hashes, and final output hashes.

The consumer pins the recipe's SHA-256, then downloads the ZIP local header,
AEA prefix, selected cluster headers, and selected encrypted segments directly
from Apple. It fetches the release key from Apple's FCS service through the
existing production HPKE implementation. Keys cross a pipe and are not written
to disk or embedded in the recipe.

After ciphertext hash checks, native AEA HMAC verification and LZFSE decoding,
the consumer reconstructs the compressed APFS storage directly from plaintext
spans. It decompresses the resource forks/attributes and applies the requested
output conversion. It does not fetch directory trees, mount APFS, or create a
sparse/full disk image. Files are published only after **all** output hashes
have passed. `--file` selects a subset and removes unrelated dependencies.

The six Wi-Fi outputs require four AEA segments and two cluster headers, with
eight range requests in total. The seven-file demonstration requires five
segments, three cluster headers, and ten requests.

## Trust and fallback

The recipe has no firmware bytes, decrypted dictionaries, or encryption keys.
It contains byte locations and validation metadata. Selected cluster MACs
are anchored by the independently pinned recipe; the preparation verified the
full header chain. The consumer verifies selected content, **not** the full
IPSW SHA-256. It cannot verify bytes it did not download.

For installer integration, the signed catalog must authenticate the recipe and
its exact decoder/source versions. The CLI's `--recipe-sha256` is the explicit
prototype trust input, not a replacement production signing mechanism.

Wrong range offsets/lengths, HTTP 200 instead of 206, corrupt headers, ciphertext,
storage, and final files are rejected before output publication. This prototype
stops on failure; it does not silently launch a multi-gigabyte fallback.
Integration can offer the existing full extraction path, but must retain its
full input/output hash checks. If Apple actually replaces the pinned image,
accepting different bytes requires a newly qualified and signed recipe. A
fallback must not turn an integrity failure into acceptance of unknown inputs.

Downloading from Apple instead of redistributing firmware avoids including the
blobs in our artifacts. It is not a blanket legal conclusion: Apple's licensing
terms still apply. Asahi documents the lack of a redistributable license for
these blobs; Apple's macOS license restricts redistribution. No encrypted or
decrypted Apple data is hosted by this experiment.

## Reproduce

Python 3.14 is required for baseline preparation; the tested runner was Linux
amd64. The Go helper requires Go 1.26.5 or newer. APFS and codec dependencies
are pinned in `apfs-reader/go.mod` and `go.sum`. Native macOS execution of this
new prototype has not been qualified.

Run from this directory, keeping generated artifacts outside the repository:

```bash
python3 -m venv /tmp/apple-range-venv
/tmp/apple-range-venv/bin/pip install -r requirements.txt
python3 build_helpers.py /tmp/apple-range-helpers
/tmp/apple-range-venv/bin/python -m unittest discover -p 'test_*.py' -v

/tmp/apple-range-venv/bin/python prototype.py fetch \
  --recipe evidence/j713-25G83-recipe.json \
  --recipe-sha256 94ae751fc30c4c31a4a99b4bb5d398b577e259e11f18da866ec3fbef31e7add3 \
  --key-helper /tmp/apple-range-helpers/fcs-key \
  --apfs-helper /tmp/apple-range-helpers/apfs-reader \
  --output /tmp/apple-range-extracted
```

The last command extracts all seven files. Add repeated `--file OUTPUT_NAME`
arguments to fetch only the required subset. Existing output directories are
rejected. No administrator privileges are needed.

To add files, edit a copy of `evidence/j713-25G83-files.json`. Each entry has a
source path inside APFS, a relative output name, an `identity` or `nvram`
transform, and optionally an independently known final SHA-256. No Python or Go
changes are needed for another ordinary file supported by the current codecs.

If necessary, prepare the decoded baseline once from a cached AEA:

```bash
/tmp/apple-range-venv/bin/python decode_baseline.py \
  --input /path/to/cached-system.dmg.aea \
  --lock ../../Engine/cleanroom/profiles/j713-apple-inputs.json \
  --key-helper /tmp/apple-range-helpers/fcs-key \
  --output /path/to/new-System.dmg

/tmp/apple-range-venv/bin/python prototype.py prime \
  --aea /path/to/cached-system.dmg.aea --image /path/to/System.dmg \
  --selection /path/to/files.json \
  --lock ../../Engine/cleanroom/profiles/j713-apple-inputs.json \
  --key-helper /tmp/apple-range-helpers/fcs-key \
  --apfs-helper /tmp/apple-range-helpers/apfs-reader \
  --workspace /path/to/new-private-preparation-directory \
  --recipe /path/to/new-recipe.json
```

`prime` prints the new recipe fingerprint. The preparation directory contains
locally extracted Apple bytes and must not be shipped. The recipe does not.

## Bounds of the prototype

The mechanism is file-list driven, not specific to Broadcom filenames. Current
admitted layers are a stored ZIP member, profile-1 AEA with 1 MiB segments / 256
segments per cluster / LZFSE / SHA-256, and raw APFS volume 0. APFS zlib, LZVN,
and LZFSE inline/resource-fork compression use the pinned decoder. Additional
archive profiles, DMG containers, APFS encryption, or unsupported compression
types require explicit support and new tests, not guessed offsets.

Preparation limits each file to 64 MiB. Downloading currently bounds encrypted
range memory to 256 MiB and uses up to eight workers. Production integration
still needs comprehensive recipe resource bounds, retry/progress/cancellation,
macOS packaging qualification, and the signed-recipe/fallback wiring. No app
or kernel release was built or changed for this experiment.

The seven synthetic tests cover sparse cluster reads, a partial last segment,
root/header/ciphertext tampering, fragmented storage and deduplication, NVRAM
conversion, wrong recipe fingerprints, and servers ignoring Range requests.

## Format and licensing sources

- [AEA format research](https://github.com/kinnay/AEA/blob/main/FORMAT.md)
- [ipsw AEA decoder](https://github.com/blacktop/ipsw/blob/master/pkg/aea/decrypt.go)
- [ipsw FCS key documentation](https://blacktop.github.io/ipsw/docs/guides/aea/)
- [Pinned APFS reader](https://github.com/deploymenttheory/go-apfs-v2/tree/57944b8896848630241ad9543656b17c933de89e)
- [Asahi firmware distribution explanation](https://asahilinux.org/docs/platform/open-os-interop/)
- [Apple macOS Tahoe license](https://www.apple.com/legal/sla/docs/macOSTahoe.pdf)
