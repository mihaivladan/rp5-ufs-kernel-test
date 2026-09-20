#!/usr/bin/env bash
set -euo pipefail
kit=$(pwd)
out="${kit}/rocknix-out"
mkdir -p "${out}"
export ARCH=arm64
make_args=(ARCH=arm64 CC=gcc-14 HOSTCC=gcc-14 HOSTCXX=g++-14)
export KBUILD_BUILD_USER=consoleos KBUILD_BUILD_HOST=github-arm
export KBUILD_BUILD_TIMESTAMP='2026-09-01 00:00:00 UTC'
export LOCALVERSION=
if [[ "${1:-all}" != compile ]]; then
work=$(mktemp -d /tmp/rocknix-kernel.XXXXXX)
cd "${work}"
curl -fL --retry 3 -o release.tar https://github.com/ROCKNIX/distribution/releases/download/20260901/ROCKNIX-SM8250.aarch64-20260901.tar
echo '3c23a04b76a9237b4d824819d80cc83c98ce118d33e1bd979a2a68ff40f034cb  release.tar' | sha256sum -c -
curl -fL --retry 3 -o linux.tar.xz https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.tar.xz
echo 'f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3  linux.tar.xz' | sha256sum -c -
tar -xf linux.tar.xz
source_dir="${work}/linux-7.2"
python3 "${kit}/rocknix/prepare.py" release.tar "${work}" "${source_dir}" "${kit}/upstream-rocknix" "${out}"
bash "${source_dir}/scripts/extract-ikconfig" "${work}/KERNEL" > "${out}/stock.config"
unsquashfs -d "${work}/stock-root" "${work}/SYSTEM" usr/lib/kernel-overlays/base/lib/firmware
cd "${source_dir}"
cp "${out}/stock.config" .config
python3 - "${work}/stock-root" <<'PY'
from pathlib import Path
import re, shutil, sys
config = Path('.config').read_text()
firmware = re.search(r'^CONFIG_EXTRA_FIRMWARE="([^"]+)"$', config, re.M).group(1).split()
root = Path(sys.argv[1]).resolve()
for name in firmware:
    origin = root / 'usr/lib/kernel-overlays/base/lib/firmware' / name
    if not origin.is_file() or not origin.resolve().is_relative_to(root):
        raise SystemExit(f'Missing or external firmware: {name}')
    target = Path('external-firmware') / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, target)
print('Reused stock firmware:', firmware)
PY
rm -rf "${work}/stock-root"
rm "${work}/release.tar" "${work}/SYSTEM" "${work}/linux.tar.xz"
bash scripts/kconfig/merge_config.sh -m .config "${kit}/rocknix/diagnostics.config"
scripts/config --set-str INITRAMFS_SOURCE "${work}/initramfs.cpio"
scripts/config --set-str EXTRA_FIRMWARE_DIR "${source_dir}/external-firmware"
make "${make_args[@]}" olddefconfig
cp .config "${out}/kernel.config"
python3 - "${kit}/rocknix/diagnostics.config" <<'PY'
from pathlib import Path
import sys
actual = set(Path('.config').read_text().splitlines())
requested = [line for line in Path(sys.argv[1]).read_text().splitlines() if line]
missing = [line for line in requested if line not in actual]
if missing:
    raise SystemExit('Diagnostic settings did not resolve: ' + ', '.join(missing))
# In Linux 7.2, genpd diagnostics are guarded directly by CONFIG_DEBUG_FS.
genpd = Path('drivers/pmdomain/core.c').read_text()
if '#ifdef CONFIG_DEBUG_FS' not in genpd or '"pm_genpd_summary"' not in genpd:
    raise SystemExit('Expected Linux 7.2 power-domain debugfs implementation missing')
print('All diagnostic settings verified.')
PY
cp .config "${out}/kernel.config"
make "${make_args[@]}" -j"$(nproc)" prepare
release=$(make "${make_args[@]}" -s kernelrelease)
test "${release}" = '7.2.0-consoleos-diag-ufs1'
make "${make_args[@]}" -j"$(nproc)" DTC_FLAGS=-@ qcom/sm8250-retroidpocket-rp5.dtb qcom/sm8250-retroidpocket-rp5-visionox.dtb
if [[ "${1:-all}" == prepare ]]; then
    printf 'RK_WORK_DIR=%s\n' "${work}" >> "${GITHUB_ENV:?}"
    echo 'Preparation passed: patches, diagnostics, kernel release and both RP5 DTBs.'
    exit 0
fi
else
    work="${RK_WORK_DIR:?Missing prepared kernel directory}"
fi
source_dir="${work}/linux-7.2"
cd "${source_dir}"
release=$(make "${make_args[@]}" -s kernelrelease)
test "${release}" = '7.2.0-consoleos-diag-ufs1'
make "${make_args[@]}" -j"$(nproc)" Image modules
stage="${work}/stage"
mkdir -p "${stage}/boot" "${stage}/lib/modules"
cp arch/arm64/boot/Image "${stage}/boot/KERNEL"
cp arch/arm64/boot/dts/qcom/sm8250-retroidpocket-rp5*.dtb "${stage}/boot/"
make "${make_args[@]}" INSTALL_MOD_PATH="${stage}" INSTALL_MOD_STRIP=1 modules_install
rm -f "${stage}/lib/modules/${release}/build" "${stage}/lib/modules/${release}/source"
depmod -b "${stage}" "${release}"
cp System.map Module.symvers "${out}/"
objcopy --dump-section .BTF="${out}/vmlinux.btf" vmlinux
cp drivers/ufs/host/ufs-qcom.c drivers/ufs/host/ufs-qcom.h "${out}/"
test -s "${stage}/boot/KERNEL"
test -s "${stage}/lib/modules/${release}/modules.dep"
test -s "${out}/vmlinux.btf"
tar -C "${stage}" -cf - boot lib | zstd -T0 -10 -o "${out}/rocknix-${release}.tar.zst"
cd "${out}"
sha256sum ./*.tar.zst > SHA256SUMS
printf '%s\n' 'Build and artifact checks passed. Not installed or boot-tested.' > BUILD-SUCCESS.txt
