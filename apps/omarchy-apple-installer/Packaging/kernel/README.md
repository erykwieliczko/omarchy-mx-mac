# Aurora Silicon J700 kernel build

The development Neo bundle selects **aurora-silicon-dirtyroom-J700** in Limine.
Linux comes from `aurora-silicon/linux-aurora`, branch `J700`, commit
`783d4c8ee57b9895ef140c47ce3ef8ee86d1ca17`. Its release is
`7.1.6-aurora-silicon-dirtyroom-j700.1`. M1/M2 do not use this bundle.

`build-kernel.sh SOURCE WORKSPACE RUST_TOOLCHAIN` merges the branch's
`j700_dirtyroom_gpu_defconfig` with `j700-distribution.config`, resolves Kconfig,
and rejects missing requested features. Build tools are LLVM 22.1.8, Rust 1.95.0,
bindgen 0.73.2, make, flex, bison, kmod and dtc. Workers default to 10.
The image uses 16 KiB pages, Rust/Asahi graphics, broad USB storage, network,
audio, webcam, serial and input support, containers and normal kernel hardening.
The stale `APPLE_PMP_THERMAL` option references an absent source file and is
disabled; `APPLE_PMP_V2_THERMAL` remains enabled. Device-mapper integrity is
included for the unchanged encryption hook. The resolved config and hashes ship.
`verify-kernel.py` compares every staged module with modules.order, checks vermagic,
runs depmod against System.map, and verifies ARM64 EFI and the J700 DTB.

Build stage 2 from `aurora-silicon/m1n1-aurora` J700
`b6c71c8d9840f6686677aee8475862948d0520df`, including its artwork submodule:
`make RELEASE=1 CHAINLOADING=1 T8140_KIS_PROXY=1 USE_CLANG=1 BUILDSTD=1`.
This supplies the new GPU/display handoff. The admitted persistent stage-1 base
remains unchanged; never substitute the KIS development binary for stage 1.
`build-uboot.sh SOURCE OUTPUT` builds `aurora-silicon/u-boot` J700
`b0f6d36e1c3ff4b4eceec663ded023830c237a30` with GCC's AArch64 cross compiler.
It selects the actual installation's ESP UUID and existing Limine EFI executable.
Keyboard/USB autoprobes are disabled in U-Boot; Linux initializes those devices.
The full main J700 DTB is used, not the radio DTS with hardcoded MAC addresses.
The new NVMe driver matches this DTB and the kernel's RTKit handoff.

Restore the exact original OS ZIP's root.img into a disposable root with
`btrfs restore -m -S -r 256`, then restore boot.img into its `/boot` with debugfs.
Do not mount or modify a test Mac. With ARM64 QEMU binfmt, bubblewrap, ACL tools
and zstd installed, run `build-payload.py --artifacts ... --root ...
--upstream ... --output ...`; upstream contains the original `esp/limine.conf`.
The initial UKI uses the original ARM mkinitcpio and firmware/encryption hooks.
It includes a required bootstrap service with the exact root-payload digest.
After encryption and sysroot mounting, bootstrap installs the verified store
under `/aurora/releases`, selects `/aurora/current`, links its modules, and
regenerates the UKI with the current root/LUKS arguments. Kernel-specific flags
`idle=nop arm64.nowfxt` are retained. The persistent image has no bootstrap hook.

A Limine post hook repeats the UKI generation on subsequent boot updates and
re-keying. It uses Limine's current command line, bypasses recursive hooks/locks,
retains the stock kernel entry, and sets a named default. Failed updates return
100 and restore the previous menu/UKI. Images and initramfs stay in `/aurora`;
only the bootable UKI and initial compressed payload need ESP copies.

`build-bundle.py --help` lists the final inputs. It packages binaries, receipts,
licenses and complete corresponding sources at a new immutable version URL.
The source download is separate from the installer download. Publicly verify
both archives, update the app pin, and build the development app.

Compilation is not hardware qualification. This branch contains experimental
Neo GPU/display/audio/radio code. Bluetooth's PCI driver is inventory-only by
default; the radio service enables the dynamic Wi-Fi overlay after firmware is
available. Existing Mesa is unchanged. Suspend, all peripherals and the combined
persistent boot chain require physical testing. Do not advertise this candidate
as production-qualified or expand the public supported-machine list.
