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

# Ubuntu 25.04 has glibc 2.41 and FFmpeg 7 headers. Its release is archived,
# so use the immutable old-releases archive. Final linking is against the
# checksum-verified ROCKNIX runtime libraries, not these Ubuntu libraries.
sed -i 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g; s|http://security.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
  /etc/apt/sources.list.d/ubuntu.sources
dpkg --add-architecture arm64
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  file make pkg-config patch qemu-user-static ca-certificates \
  libasound2-dev:arm64 libass-dev:arm64 libdrm-dev:arm64 \
  libegl-dev:arm64 libflac-dev:arm64 libfontconfig1-dev:arm64 \
  libfreetype-dev:arm64 libgl-dev:arm64 libglu1-mesa-dev:arm64 \
  libminiupnpc-dev:arm64 libopenal-dev:arm64 libogg-dev:arm64 \
  libpipewire-0.3-dev:arm64 libpng-dev:arm64 libpulse-dev:arm64 \
  libsdl2-dev:arm64 libssl-dev:arm64 libudev-dev:arm64 \
  libvorbis-dev:arm64 libvpx-dev:arm64 libvulkan-dev:arm64 \
  libwayland-dev:arm64 libxkbcommon-dev:arm64 liblzma-dev:arm64 \
  libavcodec-dev:arm64 libavformat-dev:arm64 libavutil-dev:arm64 \
  libswresample-dev:arm64 libswscale-dev:arm64 zlib1g-dev:arm64

rocknix/retroarch/build-standalone-aarch64.sh \
  "$source_dir" "$artifact_dir" "$target_root"
rocknix/retroarch/verify-rocknix-abi.sh \
  "$target_root" "$artifact_dir/retroarch" "$artifact_dir"
