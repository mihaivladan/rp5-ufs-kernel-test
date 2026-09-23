#!/usr/bin/env bash
set -euo pipefail

kit=$(pwd)
out="${kit}/rocknix-smem-reader-out"
reference="${kit}/rocknix/smem-reader/reference"
module_dir="${kit}/rocknix/smem-reader"
rm -rf "${out}"
mkdir -p "${out}"

export ARCH=arm64
make_args=(ARCH=arm64 CC=gcc-14 HOSTCC=gcc-14 HOSTCXX=g++-14)
export KBUILD_BUILD_USER=consoleos
export KBUILD_BUILD_HOST=github-arm
export KBUILD_BUILD_TIMESTAMP='2026-09-01 00:00:00 UTC'
export LOCALVERSION=

work=$(mktemp -d /tmp/rocknix-smem-reader.XXXXXX)
trap 'rm -rf "${work}"' EXIT
cd "${work}"

curl -fL --retry 3 -o linux.tar.xz \
    https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.tar.xz
echo 'f9fef3d14c0df53819026f4be74459835c2a0b0dcbf5b5bbd9ea19f0829402b3  linux.tar.xz' \
    | sha256sum -c -
tar -xf linux.tar.xz
source_dir="${work}/linux-7.2"

ROCKNIX_PROFILE=adsp-no-auto-ab python3 - \
    "${kit}" "${source_dir}" "${kit}/upstream-rocknix" <<'PY'
import importlib.util
from pathlib import Path
import sys

kit, source, recipe = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("rocknix_prepare", kit / "rocknix/prepare.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
records = module.apply_patches(
    recipe.resolve(), source.resolve(), (kit / "ufs-lane-clocks.patch").resolve(),
    "adsp-no-auto-ab",
)
if not records:
    raise SystemExit("No patch records produced")
PY

cd "${source_dir}"
cp "${reference}/kernel.config" .config
echo 'f79fdd98456eca600ef8e93d870f21e726511eab04c8b1965be15d157fa70f8c  .config' \
    | sha256sum -c -
make "${make_args[@]}" olddefconfig
cmp -s .config "${reference}/kernel.config" || {
    diff -u "${reference}/kernel.config" .config || true
    echo 'Exact Phase 6H config changed during olddefconfig' >&2
    exit 1
}
make "${make_args[@]}" -j"$(nproc)" prepare modules_prepare
release=$(make "${make_args[@]}" -s kernelrelease)
test "${release}" = '7.2.0-consoleos-adspab1'

cp "${reference}/Module.symvers" Module.symvers
echo '6acc633db21a4affabf23de903cdf50ead252c963ca9acff075c1b999d025622  Module.symvers' \
    | sha256sum -c -
make "${make_args[@]}" -j"$(nproc)" M="${module_dir}" modules

module="${module_dir}/consoleos_smem_reader.ko"
test -s "${module}"
test "$(modinfo -F license "${module}")" = GPL
test "$(modinfo -F vermagic "${module}" | awk '{print $1}')" = \
    '7.2.0-consoleos-adspab1'
nm -u "${module}" | grep -Eq '[[:space:]]U qcom_smem_get$'
if nm -u "${module}" | grep -Eq '[[:space:]]U qcom_smem_alloc$'; then
    echo 'Reader must not import qcom_smem_alloc' >&2
    exit 1
fi

cp "${module}" "${out}/"
cp "${module_dir}/consoleos_smem_reader.c" "${out}/"
cp "${module_dir}/Makefile" "${out}/module.Makefile"
cp "${reference}/kernel.config" "${out}/"
cp "${reference}/Module.symvers" "${out}/"
modinfo "${module}" > "${out}/modinfo.txt"
nm -u "${module}" > "${out}/undefined-symbols.txt"
(
    cd "${out}"
    sha256sum consoleos_smem_reader.ko consoleos_smem_reader.c \
        module.Makefile kernel.config Module.symvers > SHA256SUMS
    sha256sum -c SHA256SUMS
)

python3 - "${out}" <<'PY'
from pathlib import Path
import json
import sys

out = Path(sys.argv[1])
(out / "provenance.json").write_text(json.dumps({
    "baseline": "ROCKNIX 20260901 / Linux 7.2.0",
    "target_release": "7.2.0-consoleos-adspab1",
    "rocknix_revision": "1ebff24f36501fb6493beb2bf83bf2604536d9aa",
    "phase6h_kernel_config_sha256": "f79fdd98456eca600ef8e93d870f21e726511eab04c8b1965be15d157fa70f8c",
    "phase6h_module_symvers_sha256": "6acc633db21a4affabf23de903cdf50ead252c963ca9acff075c1b999d025622",
    "build": "Exact patched source prepared; external diagnostic module only",
    "interface": "Read-only debugfs lookup of ADSP SMEM items 606, 624 and 634",
    "deployment": "Not installed or boot-tested",
}, indent=2) + "\n")
PY

printf '%s\n' \
    'Exact Phase 6H external-module build passed. No kernel, DTB or kernel module set was rebuilt.' \
    > "${out}/BUILD-SUCCESS.txt"
