#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 RELEASE_URL RELEASE_SHA256 TARGET_ROOT" >&2
  exit 2
fi

release_url=$1
release_sha=$2
target_root=$3
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

curl -fL --retry 3 -o "$work/release.tar" "$release_url"
printf '%s  %s\n' "$release_sha" "$work/release.tar" | sha256sum -c -
tar -xf "$work/release.tar" -C "$work" --wildcards --no-anchored 'SYSTEM'
system_image=$(find "$work" -type f -name SYSTEM -print -quit)
test -n "$system_image"
rm -rf "$target_root"
unsquashfs -no-progress -d "$target_root" "$system_image"
test -x "$target_root/usr/bin/retroarch"
test -e "$target_root/usr/lib/libc.so.6"
