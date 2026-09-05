#!/bin/bash

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT
builder=$ROOT/apps/omarchy-apple-installer/Packaging/build-dmg.sh
mkdir "$test_tmp/tools" "$test_tmp/input files" "$test_tmp/output files"
export PATH="$test_tmp/tools:$PATH"

cat >"$test_tmp/tools/ditto" <<'EOF'
#!/bin/bash
set -euo pipefail
cp "$1" "$2"
EOF
cat >"$test_tmp/tools/pkgutil" <<'EOF'
#!/bin/bash
set -euo pipefail
[[ $1 == "--check-signature" && $(cat "$2") == "signed fixture" ]]
EOF
cat >"$test_tmp/tools/hdiutil" <<'EOF'
#!/bin/bash
set -euo pipefail
operation=$1
shift
case "$operation" in
  create)
    [[ ${DMG_TEST_FAILURE:-} != "create" ]] || exit 1
    output=${!#}
    source=""
    while (( $# > 0 )); do
      case "$1" in
        -srcfolder) source=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    tar -cf "$output" -C "$source" .
    ;;
  verify)
    [[ ${DMG_TEST_FAILURE:-} != "verify" ]] || exit 1
    tar -tf "${!#}" >/dev/null
    ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$test_tmp/tools/"*

package="$test_tmp/input files/Omarchy Installer.pkg"
output="$test_tmp/output files/Omarchy Installer.dmg"
printf 'signed fixture\n' >"$package"
bash "$builder" "$package" "$output" >"$test_tmp/build.log"
tar -xOf "$output" './Omarchy Installer.pkg' >"$test_tmp/extracted.pkg"
cmp -s "$package" "$test_tmp/extracted.pkg" || fail "package bytes survive wrapping"
tar -xOf "$output" ./INSTALL.txt | grep -Fq 'Open the .pkg' \
  || fail "image explains how to open the package"
grep -Fq 'DMG_BUILT:' "$test_tmp/build.log" || fail "builder reports its output"
pass "disk image preserves the package and includes installation instructions"

cp "$output" "$test_tmp/original.dmg"
if bash "$builder" "$package" "$output" >/dev/null 2>&1; then
  fail "builder refuses an existing image"
fi
cmp -s "$output" "$test_tmp/original.dmg" || fail "existing image remains unchanged"
ln -s "$test_tmp/missing" "$test_tmp/link.dmg"
if bash "$builder" "$package" "$test_tmp/link.dmg" >/dev/null 2>&1; then
  fail "builder refuses a dangling output symlink"
fi
pass "disk image creation refuses existing outputs and symlinks"

printf 'unsigned fixture\n' >"$test_tmp/unsigned.pkg"
if bash "$builder" "$test_tmp/unsigned.pkg" "$test_tmp/unsigned.dmg" >/dev/null 2>&1; then
  fail "builder refuses a package that fails signature verification"
fi
[[ ! -e $test_tmp/unsigned.dmg ]] || fail "rejected package produces no image"
for failure in create verify; do
  if DMG_TEST_FAILURE=$failure bash "$builder" "$package" "$test_tmp/$failure.dmg" >/dev/null 2>&1; then
    fail "builder propagates image $failure failure"
  fi
  [[ ! -e $test_tmp/$failure.dmg ]] || fail "failed image is not published"
done
pass "signature, creation, and verification failures leave no final image"
