#!/usr/bin/env bash
set -euo pipefail
kit=$(pwd)
mkdir -p aop-out
out="$kit/aop-out"
work=$(mktemp -d)
curl -fL --retry 3 -o "$work/linux.tar.xz" https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.tar.xz
echo 'f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3  '"$work/linux.tar.xz" | sha256sum -c -
tar -C "$work" -xf "$work/linux.tar.xz"
source_dir="$work/linux-7.2"
python3 - "$kit" "$source_dir" <<'PY'
import importlib.util, sys
from pathlib import Path
kit, source = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('prepare', kit/'rocknix/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
prepare.apply_patches(kit/'upstream-rocknix', source, kit/'ufs-lane-clocks.patch')
PY
cp aop-diag/stock.config "$source_dir/.config"
cp aop-diag/reference.symvers "$source_dir/Module.symvers"
cd "$source_dir"
export ARCH=arm64 LOCALVERSION=
scripts/config --disable LOCALVERSION_AUTO
make ARCH=arm64 CC=gcc-14 HOSTCC=gcc-14 olddefconfig
test "$(make -s ARCH=arm64 CC=gcc-14 kernelrelease)" = 7.2.0
make -j"$(nproc)" ARCH=arm64 CC=gcc-14 HOSTCC=gcc-14 modules_prepare
make -j"$(nproc)" ARCH=arm64 CC=gcc-14 HOSTCC=gcc-14 M="$kit/aop-diag" W=1 modules
cp "$kit/aop-diag/rp5_aop_diag.ko" "$out/"
cp .config "$out/build.config"
modinfo "$out/rp5_aop_diag.ko" | tee "$out/modinfo.txt"
test "$(modinfo -F vermagic "$out/rp5_aop_diag.ko" | xargs)" = '7.2.0 SMP preempt mod_unload aarch64'
git -C "$kit" rev-parse HEAD > "$out/source-commit.txt"
cd "$out"
sha256sum rp5_aop_diag.ko build.config modinfo.txt source-commit.txt > SHA256SUMS
