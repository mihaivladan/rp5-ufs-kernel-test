#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 RETROARCH_SOURCE ARTIFACT_DIR TARGET_ROOT" >&2
  exit 2
fi

source_dir=$1
artifact_dir=$2
target_root=$3
target_lib=$target_root/usr/lib
target_link=$PWD/rocknix-target-link

test -x "$source_dir/configure"
test -x "$target_root/usr/bin/retroarch"
mkdir -p "$artifact_dir" "$target_link"

export CC=aarch64-linux-gnu-gcc
export CXX=aarch64-linux-gnu-g++
export AR=aarch64-linux-gnu-ar
export AS=aarch64-linux-gnu-as
export LD=aarch64-linux-gnu-ld
export NM=aarch64-linux-gnu-nm
export OBJCOPY=aarch64-linux-gnu-objcopy
export OBJDUMP=aarch64-linux-gnu-objdump
export RANLIB=aarch64-linux-gnu-ranlib
export READELF=aarch64-linux-gnu-readelf
export STRIP=aarch64-linux-gnu-strip
export PKG_CONFIG=pkg-config
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
export PKG_CONFIG_PATH=
export CFLAGS="-O2 -pipe -DUDEV_TOUCH_SUPPORT"
export CXXFLAGS="-O2 -pipe -DUDEV_TOUCH_SUPPORT"

# Build an unversioned link view from the exact target libraries. Ubuntu
# supplies headers and pkg-config metadata only; every shared-library lookup is
# directed to the extracted ROCKNIX runtime first.
while IFS= read -r library; do
  soname=$($READELF -d "$library" 2>/dev/null | \
    sed -n 's/.*(SONAME).*\[\(.*\)\].*/\1/p' | head -1)
  [ -n "$soname" ] || continue
  ln -sfn "$library" "$target_link/$soname"
  link_name=${soname%%.so.*}.so
  ln -sfn "$library" "$target_link/$link_name"
done < <(find "$target_lib" -type f -name '*.so*' | LC_ALL=C sort)

interpreter=$($READELF -l "$target_root/usr/bin/retroarch" | \
  sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')
test -n "$interpreter"
export LDFLAGS="-L$target_link -Wl,-rpath-link,$target_lib -Wl,--dynamic-linker=$interpreter"

configure_flags=(
  --host=aarch64-linux-gnu
  --disable-qt
  --enable-alsa
  --enable-udev
  --disable-opengl1
  --disable-x11
  --enable-zlib
  --enable-freetype
  --disable-discord
  --disable-vg
  --disable-sdl
  --enable-sdl2
  --enable-kms
  --enable-ffmpeg
  --disable-neon
  --enable-wayland
  --enable-opengl
  --disable-opengles
  --disable-opengles3
  --disable-opengles3_1
  --disable-opengles3_2
  --enable-vulkan
  --enable-vulkan_display
)

make_flags=(
  HAVE_UPDATE_ASSETS=0
  HAVE_LIBRETRODB=1
  HAVE_BLUETOOTH=0
  HAVE_NETWORKING=1
  HAVE_ZARCH=1
  HAVE_QT=0
  HAVE_LANGEXTRA=1
)

{
  printf 'env CC=%q CXX=%q AR=%q AS=%q LD=%q NM=%q OBJCOPY=%q OBJDUMP=%q RANLIB=%q READELF=%q STRIP=%q ' \
    "$CC" "$CXX" "$AR" "$AS" "$LD" "$NM" "$OBJCOPY" "$OBJDUMP" \
    "$RANLIB" "$READELF" "$STRIP"
  printf 'PKG_CONFIG=%q PKG_CONFIG_LIBDIR=%q PKG_CONFIG_PATH=%q CFLAGS=%q CXXFLAGS=%q LDFLAGS=%q ' \
    "$PKG_CONFIG" "$PKG_CONFIG_LIBDIR" "$PKG_CONFIG_PATH" "$CFLAGS" "$CXXFLAGS" "$LDFLAGS"
  printf './configure'
  printf ' %q' "${configure_flags[@]}"
  printf '\nmake -j%q' "$(nproc)"
  printf ' %q' "${make_flags[@]}"
  printf '\n'
} > "$artifact_dir/BUILD-COMMAND.txt"
printf '%s\n' "${configure_flags[@]}" > "$artifact_dir/CONFIGURE-FLAGS.txt"
printf '%s\n' "${make_flags[@]}" > "$artifact_dir/MAKE-FLAGS.txt"
{
  "$CC" --version | head -1
  "$LD" --version | head -1
  dpkg-query -W -f='${Package}=${Version}\n' \
    gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu
} > "$artifact_dir/TOOLCHAIN.txt"

cd "$source_dir"
./configure "${configure_flags[@]}"
make -j"$(nproc)" "${make_flags[@]}"

file retroarch
"$READELF" -h retroarch | grep -F 'Machine:'
install -m 0755 retroarch "$artifact_dir/retroarch"
install -m 0644 config.log config.mk "$artifact_dir/"
