#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 TARGET_ROOT CANDIDATE_BINARY REPORT_DIR" >&2
  exit 2
fi

target_root=$1
candidate=$2
report_dir=$3
stock=$target_root/usr/bin/retroarch
mkdir -p "$report_dir"

needed() {
  aarch64-linux-gnu-readelf -d "$1" | \
    sed -n 's/.*(NEEDED).*\[\(.*\)\].*/\1/p' | LC_ALL=C sort -u
}

needed "$stock" > "$report_dir/STOCK-NEEDED.txt"
needed "$candidate" > "$report_dir/CANDIDATE-NEEDED.txt"

missing=0
while IFS= read -r soname; do
  if ! find "$target_root" \( -type f -o -type l \) -name "$soname" \
      -print -quit | grep -q .; then
    echo "missing target soname: $soname" >&2
    missing=1
  fi
done < "$report_dir/CANDIDATE-NEEDED.txt"
test "$missing" -eq 0

required_versions() {
  aarch64-linux-gnu-readelf --version-info "$1" | \
    sed -n 's/.*Name: \(\(GLIBC\|GLIBCXX\|GCC\)_[^ ]*\).*/\1/p' | \
    LC_ALL=C sort -u
}

required_versions "$candidate" > "$report_dir/CANDIDATE-SYMBOL-VERSIONS.txt"
{
  find "$target_root/usr/lib" \( -type f -o -type l \) \
    \( -name 'libc.so.6' -o -name 'libstdc++.so.6*' -o -name 'libgcc_s.so.1' \) \
    -exec strings {} +
} | grep -E '^(GLIBC|GLIBCXX|GCC)_[0-9]' | LC_ALL=C sort -u \
  > "$report_dir/ROCKNIX-SYMBOL-VERSIONS.txt"
if ! comm -23 "$report_dir/CANDIDATE-SYMBOL-VERSIONS.txt" \
    "$report_dir/ROCKNIX-SYMBOL-VERSIONS.txt" \
    > "$report_dir/MISSING-SYMBOL-VERSIONS.txt"; then
  exit 1
fi
test ! -s "$report_dir/MISSING-SYMBOL-VERSIONS.txt"

stock_interpreter=$(aarch64-linux-gnu-readelf -l "$stock" | \
  sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')
candidate_interpreter=$(aarch64-linux-gnu-readelf -l "$candidate" | \
  sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')
test "$candidate_interpreter" = "$stock_interpreter"

qemu-aarch64-static -L "$target_root" "$stock" --features \
  > "$report_dir/STOCK-FEATURES.txt"
qemu-aarch64-static -L "$target_root" "$candidate" --features \
  > "$report_dir/CANDIDATE-FEATURES.txt"
diff -u "$report_dir/STOCK-FEATURES.txt" "$report_dir/CANDIDATE-FEATURES.txt" \
  > "$report_dir/FEATURE-DIFF.txt"

{
  printf 'abi_gate=pass\n'
  printf 'needed_sonames=all-present-in-rocknix-runtime\n'
  printf 'symbol_versions=all-provided-by-rocknix-runtime\n'
  printf 'program_interpreter=%s\n' "$candidate_interpreter"
  printf 'feature_parity=pass\n'
  printf 'device_smoke=pending\n'
} > "$report_dir/ABI-REPORT.txt"
