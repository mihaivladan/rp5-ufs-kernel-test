#!/usr/bin/env bash
# Runs on the Linux build host; checks package contents without installing them.
set -euo pipefail
cd "${1:?Pass the kernel output directory}"
release=7.2.3-consoleos-ufs1
package="armada-kernel-${release}.tar.zst"
sha256sum -c "${package}.sha256"
tar --zstd -tf "${package}" > package-files.txt
for member in \
    "lib/modules/${release}/vmlinuz" \
    "lib/modules/${release}/modules.dep" \
    "lib/modules/${release}/dtb/qcom/sm8250-retroidpocket-rp5.dtb" \
    "lib/modules/${release}/dtb/qcom/sm8250-retroidpocket-rp5-visionox.dtb"; do
    grep -Fxq "${member}" package-files.txt || { echo "Missing ${member}" >&2; exit 1; }
done
if grep '^lib/modules/' package-files.txt | grep -v "^lib/modules/${release}/\|^lib/modules/$"; then
    echo 'Unexpected kernel release in package' >&2
    exit 1
fi
grep -qx 'CONFIG_LOCALVERSION="-consoleos-ufs1"' kernel.config
grep -qx 'CONFIG_SCSI_UFS_QCOM=y' kernel.config
grep -qx 'CONFIG_DEBUG_INFO_BTF=y' kernel.config
test -s System.map
grep -q 'host->rx_lane0_sync_clk' ufs-qcom.c
if grep -q 'devm_clk_bulk_get_all' ufs-qcom.c; then
    echo 'Unexpected unpatched bulk clock acquisition' >&2
    exit 1
fi
echo 'Artifact checks passed; this is not a device boot test.'
