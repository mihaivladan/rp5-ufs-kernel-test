#!/usr/bin/env python3
"""Prepare a disposable, pinned recipe checkout. Does not build or deploy."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REVISION = "24d88394fd6ae777e7616c3ec245a7a35b5d2f81"
FIX = "f07317a8d57f382ec505597816271dd72ffa20c7"
PATCH_SHA256 = "67340039b880e85485875d896fc2e9e517e9a808beba996f1e8b151853397fd5"
RELEASE = "7.2.3-consoleos-ufs1"


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    require(len(sys.argv) == 2, "Usage: python3 prepare.py UPSTREAM_CHECKOUT")
    upstream = Path(sys.argv[1]).resolve()
    kit = Path(__file__).resolve().parent
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    require(revision == REVISION, "Unexpected upstream source revision")
    changes = subprocess.check_output(
        ["git", "-C", str(upstream), "status", "--porcelain", "--", "kernel", "toolchain.env"],
        text=True,
    )
    require(not changes, "Recipe must be clean; use a fresh disposable checkout")
    kernel = upstream / "kernel"
    require((kernel / "BASE.env").read_text().strip() == "VERSION=7.2.3", "Unexpected kernel base")
    patch = (kit / "ufs-lane-clocks.patch").read_bytes()
    require(hashlib.sha256(patch).hexdigest() == PATCH_SHA256, "Patch checksum mismatch")
    series = kernel / "patches/series"
    entries = [line.split("#")[0].strip() for line in series.read_text().splitlines()]
    entries = [line for line in entries if line]
    require(len(entries) == 144, "Expected the installed recipe's 144 patches")
    require(all((kernel / "patches" / entry).is_file() for entry in entries), "Missing recipe patch")

    config = kernel / "config/armada-kernel.config.overrides"
    original_config = config.read_text()
    require("CONFIG_LOCALVERSION" not in original_config, "Review existing local-version settings")
    build = kernel / "scripts/build-kernel.sh"
    original_build = build.read_text()
    marker = '# ---------- 5. Build ----------'
    stage_marker = '# ---------- 7. Package ----------'
    version_marker = 'KVER=$(make "${MAKE_ARGS[@]}" -s kernelrelease)'
    require(original_build.count(marker) == 1 and original_build.count(stage_marker) == 1,
            "Unexpected build-script layout")
    require(original_build.count(version_marker) == 1, "Unexpected kernelrelease query")

    # All preconditions checked before changing this disposable checkout.
    name = "9999-consoleos-ufs-lane-clocks.patch"
    (kernel / "patches" / name).write_bytes(patch)
    series.write_text(series.read_text().rstrip() + "\n" + name + "\n")
    config.write_text(original_config.rstrip() + '\nCONFIG_LOCALVERSION="-consoleos-ufs1"\n# CONFIG_LOCALVERSION_AUTO is not set\n')
    # kernelrelease explicitly skips config synchronization. Refresh auto.conf
    # after merging the fragment, before querying its CONFIG_LOCALVERSION.
    guarded = original_build.replace(version_marker, '''make "${MAKE_ARGS[@]}" prepare
cp .config "${OUT_DIR}/kernel.config"
''' + version_marker)
    guarded = guarded.replace(marker, '''# Refuse a package that could be mistaken for the installed kernel.
test "${KVER}" = "7.2.3-consoleos-ufs1"
grep -qx 'CONFIG_SCSI_UFS_QCOM=y' .config
grep -qx 'CONFIG_DEBUG_INFO_BTF=y' .config
''' + marker)
    guarded = guarded.replace(stage_marker, '''# Preserve resolved configuration and the patched sources for review.
cp .config "${OUT_DIR}/kernel.config"
cp System.map "${OUT_DIR}/System.map"
cp drivers/ufs/host/ufs-qcom.c drivers/ufs/host/ufs-qcom.h "${OUT_DIR}/"
''' + stage_marker)
    build.write_text(guarded)
    provenance = {
        "upstream_repository": "https://github.com/armada-os/armada-packages",
        "upstream_revision": REVISION,
        "baseline_patches": 144,
        "fix_commit": FIX,
        "fix_patch_sha256": PATCH_SHA256,
        "kernel_release": RELEASE,
        "baseline_kernel_package_digest": "sha256:4659cccbecd0015e5ab6ffb566d59e19b5514f889914b7ecbba2dad65addb550",
        "baseline_build_run": "https://github.com/armada-os/armada-packages/actions/runs/34767879850",
        "changes": ["UFS lane-clock fix", "unique CONFIG_LOCALVERSION", "release/config checks and diagnostic exports"],
        "deployment": "none; build artifact only",
    }
    (kit / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Prepared {RELEASE}: 144 original patches + one UFS fix")


if __name__ == "__main__":
    main()
