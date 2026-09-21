#!/usr/bin/env bash
set -euo pipefail
kit=$(pwd)
out="${kit}/rocknix-out"
mkdir -p "${out}"
profile="${ROCKNIX_PROFILE:-diagnostic}"
case "${profile}" in
diagnostic)
    config_fragment="${kit}/rocknix/diagnostics.config"
    expected_release='7.2.0-consoleos-diag-ufs2'
    artifact_name="rocknix-${expected_release}.tar.zst"
    ;;
minimal-sleep)
    config_fragment="${kit}/rocknix/minimal-sleep.config"
    expected_release='7.2.0-consoleos-minsleep1'
    artifact_name="rocknix-${expected_release}.tar.zst"
    ;;
*)
    echo "Unknown ROCKNIX_PROFILE: ${profile}" >&2
    exit 2
    ;;
esac
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
bash scripts/kconfig/merge_config.sh -m .config "${config_fragment}"
scripts/config --set-str INITRAMFS_SOURCE "${work}/initramfs.cpio"
scripts/config --set-str EXTRA_FIRMWARE_DIR "${source_dir}/external-firmware"
make "${make_args[@]}" olddefconfig
cp .config "${out}/kernel.config"
python3 - "${config_fragment}" "${profile}" <<'PY'
from pathlib import Path
import sys
actual = set(Path('.config').read_text().splitlines())
requested = [line for line in Path(sys.argv[1]).read_text().splitlines()
             if line.startswith('CONFIG_') or line.startswith('# CONFIG_')]
enabled = {line.split('=', 1)[0] for line in actual if line.startswith('CONFIG_')}
missing = []
for line in requested:
    if line.startswith('# CONFIG_') and line.endswith(' is not set'):
        symbol = line[2:-11]
        # Kconfig may omit an unset child symbol entirely when its parent is off.
        # Both forms mean that the feature cannot be built.
        if symbol in enabled:
            missing.append(line)
    elif line not in actual:
        missing.append(line)
if missing:
    raise SystemExit('Requested settings did not resolve: ' + ', '.join(missing))
# In Linux 7.2, genpd diagnostics are guarded directly by CONFIG_DEBUG_FS.
genpd = Path('drivers/pmdomain/core.c').read_text()
if '#ifdef CONFIG_DEBUG_FS' not in genpd or '"pm_genpd_summary"' not in genpd:
    raise SystemExit('Expected Linux 7.2 power-domain debugfs implementation missing')
if sys.argv[2] == 'minimal-sleep':
    required = {
        'CONFIG_ARM_PSCI_CPUIDLE=y', 'CONFIG_ARM_PSCI_CPUIDLE_DOMAIN=y',
        'CONFIG_INPUT_PM8941_PWRKEY=y', 'CONFIG_MMC_SDHCI_MSM=y',
        'CONFIG_QCOM_AOSS_QMP=y', 'CONFIG_QCOM_RPMH=y',
        'CONFIG_QCOM_RPMHPD=y', 'CONFIG_QCOM_STATS=y',
        'CONFIG_REGULATOR_QCOM_RPMH=y', 'CONFIG_QCOM_CLK_RPMH=y',
        'CONFIG_INTERCONNECT_QCOM_SM8250=y', 'CONFIG_EXT4_FS=y',
        'CONFIG_SQUASHFS=y', 'CONFIG_VFAT_FS=y',
    }
    absent = sorted(required - actual)
    if absent:
        raise SystemExit('Minimal boot requirements missing: ' + ', '.join(absent))
print(f'All {sys.argv[2]} settings verified.')
PY
cp .config "${out}/kernel.config"
make "${make_args[@]}" -j"$(nproc)" prepare
release=$(make "${make_args[@]}" -s kernelrelease)
test "${release}" = "${expected_release}"
make "${make_args[@]}" -j"$(nproc)" DTC_FLAGS=-@ qcom/sm8250-retroidpocket-rp5.dtb
if [[ "${1:-all}" == prepare ]]; then
    printf 'RK_WORK_DIR=%s\n' "${work}" >> "${GITHUB_ENV:?}"
    echo 'Preparation passed: patches, diagnostics, kernel release and the installed RP5 board DTB.'
    exit 0
fi
else
    work="${RK_WORK_DIR:?Missing prepared kernel directory}"
fi
source_dir="${work}/linux-7.2"
cd "${source_dir}"
release=$(make "${make_args[@]}" -s kernelrelease)
test "${release}" = "${expected_release}"
if [[ "${profile}" == diagnostic ]]; then
    # The previous build exposed an uninitialized cstate pointer in this file.
    printf '\nCFLAGS_dpu_crtc.o += -Werror=uninitialized -Werror=maybe-uninitialized\n' >> drivers/gpu/drm/msm/disp/dpu1/Makefile
fi
make "${make_args[@]}" -j"$(nproc)" Image modules
stage="${work}/stage"
mkdir -p "${stage}/boot" "${stage}/lib/modules"
cp arch/arm64/boot/Image "${stage}/boot/KERNEL"
cp arch/arm64/boot/dts/qcom/sm8250-retroidpocket-rp5*.dtb "${stage}/boot/"
make "${make_args[@]}" INSTALL_MOD_PATH="${stage}" INSTALL_MOD_STRIP=1 modules_install
rm -f "${stage}/lib/modules/${release}/build" "${stage}/lib/modules/${release}/source"
depmod -b "${stage}" "${release}"
cp System.map Module.symvers "${out}/"
cp drivers/ufs/host/ufs-qcom.c drivers/ufs/host/ufs-qcom.h "${out}/"
if [[ "${profile}" == diagnostic ]]; then
    objcopy --dump-section .BTF="${out}/vmlinux.btf" vmlinux
    cp drivers/gpu/drm/msm/disp/dpu1/dpu_crtc.c drivers/gpu/drm/msm/disp/dpu1/dpu_crtc.o "${out}/"
    objdump -drS drivers/gpu/drm/msm/disp/dpu1/dpu_crtc.o > "${out}/dpu_crtc-disassembly.txt"
fi
test -s "${stage}/boot/KERNEL"
test -s "${stage}/lib/modules/${release}/modules.dep"
if [[ "${profile}" == diagnostic ]]; then
    test -s "${out}/vmlinux.btf"
fi
tar -C "${stage}" -cf - boot lib | zstd -T0 -10 -o "${out}/${artifact_name}"
cd "${out}"
sha256sum "${artifact_name}" > SHA256SUMS
printf '%s\n' "Build and artifact checks passed for ${profile}. Not installed or boot-tested." > BUILD-SUCCESS.txt
