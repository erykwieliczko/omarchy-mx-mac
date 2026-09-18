echo "Install missing headers for the Omarchy or T2 kernel"

# Apple Silicon runs linux-asahi or linux-aurora, whose headers ship with the
# base install; neither x86 kernel belongs there.
omarchy-hw-apple-silicon && exit 0

# Fresh ISO installs mark earlier migrations complete, so the kernel migration
# cannot repair headers omitted by those installers. Package installation is
# idempotent when another user has already applied this repair.
for kernel in linux-omarchy linux-t2; do
  if omarchy-pkg-present "$kernel"; then
    omarchy-pkg-add "$kernel-headers"
  fi
done
