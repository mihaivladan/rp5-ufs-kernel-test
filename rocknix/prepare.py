#!/usr/bin/env python3
"""Reuse checksum-verified stock boot assets and exact-release kernel patches."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zlib

STOCK_KERNEL_SHA = "758d4a9e31ebdfe125369f82158f038521eee5e4e5d7b7e5b2da0dddee499ea4"
FIX_SHA = "67340039b880e85485875d896fc2e9e517e9a808beba996f1e8b151853397fd5"
REVISION = "1ebff24f36501fb6493beb2bf83bf2604536d9aa"


def require(ok, message):
    if not ok:
        raise SystemExit(message)


def cpio_names(data):
    offset = 0
    names = []
    while data[offset:offset + 6] in (b"070701", b"070702"):
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        size, namelen = fields[6], fields[11]
        name = data[offset + 110:offset + 110 + namelen - 1].decode()
        offset = (offset + 110 + namelen + 3) & ~3
        require(offset + size <= len(data), "Truncated initramfs entry")
        offset = (offset + size + 3) & ~3
        if name == "TRAILER!!!":
            return names
        require(not name.startswith("/") and ".." not in Path(name).parts, "Unexpected initramfs path")
        names.append(name)
    raise SystemExit("Incomplete stock initramfs")


def extract_stock(archive, work):
    with tarfile.open(archive) as tf:
        for basename in ("KERNEL", "SYSTEM"):
            matches = [m for m in tf.getmembers() if m.isfile() and Path(m.name).name == basename]
            require(len(matches) == 1, f"Expected exactly one stock {basename}")
            with tf.extractfile(matches[0]) as source, (work / basename).open("wb") as target:
                shutil.copyfileobj(source, target)
    image = (work / "KERNEL").read_bytes()
    require(hashlib.sha256(image).hexdigest() == STOCK_KERNEL_SHA, "Release kernel differs from saved device baseline")
    require(image[56:60] == b"ARMd", "Expected stock flat ARM64 Image")
    candidates = []
    offset = 0
    while True:
        offset = image.find(b"\x1f\x8b\x08", offset)
        if offset < 0:
            break
        try:
            decoder = zlib.decompressobj(31)
            raw = decoder.decompress(image[offset:], 64 * 1024 * 1024)
            if decoder.eof and raw.startswith(b"070701"):
                names = cpio_names(raw)
                if "init" in names:
                    require(not any(".ko" in n or n.startswith("lib/modules/") for n in names),
                            "Stock initramfs contains version-specific kernel modules")
                    candidates.append(raw)
        except zlib.error:
            pass
        offset += 3
    require(len(candidates) == 1, "Expected one module-free stock initramfs")
    (work / "initramfs.cpio").write_bytes(candidates[0])
    return hashlib.sha256(candidates[0]).hexdigest()


def apply_patches(repo, source, fix, profile):
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    require(revision == REVISION, "Incorrect ROCKNIX source revision")
    p = repo / "projects/ROCKNIX/packages/linux/patches"
    project = repo / "projects/ROCKNIX/patches/linux"
    device = repo / "projects/ROCKNIX/devices/SM8250/patches/linux"
    # Exact scripts/unpack order for this release. No linux/SM8250/default
    # package subdirectories or project patch directories exist at this pin.
    directories = [p, p / "aarch64", p / "mainline", p / "SM8250", p / "default",
                   p / "7.2", p / "7.2/aarch64", project, project / "aarch64",
                   project / "7.2", device]
    patches = [patch for directory in directories for patch in sorted(directory.glob("*.patch"))]
    require(len(patches) == 35, "Unexpected release patch count; review selection")
    require(hashlib.sha256(fix.read_bytes()).hexdigest() == FIX_SHA, "UFS fix checksum mismatch")
    records = []
    display_fix = Path(__file__).resolve().parent / "dpu-cstate-init.patch"
    require(hashlib.sha256(display_fix.read_bytes()).hexdigest() ==
            "e4ea3fd8ff2f5f036544abaacace08f7eb0d9e85a67ca150104dfd4f0dd41072",
            "DPU initialization fix checksum mismatch")
    extra_patches = []
    if profile == "gmu-clock-reset":
        gmu_reset = Path(__file__).resolve().parent / "a6xx-gmu-clock-reset.patch"
        require(gmu_reset.is_file(), "Missing GMU clock-reset patch")
        require(hashlib.sha256(gmu_reset.read_bytes()).hexdigest() ==
                "91e28ba23946dae574a2fc86e534f143c598a4ac044540e7bdfd38ab20f0d588",
                "GMU clock-reset patch checksum mismatch")
        extra_patches.append(gmu_reset)
    elif profile == "gpu-rpmh-fix":
        rpmh_fix = Path(__file__).resolve().parent / "a6xx-stale-rpmh-votes.patch"
        require(rpmh_fix.is_file(), "Missing upstream stale RPMh vote fix")
        require(hashlib.sha256(rpmh_fix.read_bytes()).hexdigest() ==
                "6fa32840101b0b03554173ffb0a70b059f5acfbb81147f884d9cf12219c9d5ca",
                "GPU stale RPMh vote fix checksum mismatch")
        extra_patches.append(rpmh_fix)
    for patch in patches + [fix, display_fix, *extra_patches]:
        print(f"Applying {patch.name}", flush=True)
        payload = patch.read_text().replace("@TARGET_CPU@", "cortex-a76.cortex-a55").replace("@DEVICE@", "SM8250")
        # Preserve upstream's patch behavior; never skip a failed patch.
        args = ["patch", "-p1", "--batch", "--forward"]
        if patch in (display_fix, *extra_patches):
            args.append("--fuzz=0")
        subprocess.run(args, input=payload, text=True, cwd=source, check=True)
        records.append({"path": str(patch.relative_to(repo)) if patch in patches else patch.name,
                        "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()})
    if profile == "gpu-rpmh-fix":
        gmu_source = (source / "drivers/gpu/drm/msm/adreno/a6xx_gmu.c").read_text()
        require("if (!test_and_clear_bit(GMU_STATUS_FW_START, &gmu->status))" in gmu_source,
                "Corrected GMU firmware-start condition missing")
        require("gmu_write(gmu, REG_A6XX_GMU_CM3_SYSRESET, 1);" in gmu_source,
                "GMU CM3 reset before RPMh stop missing")
    dts = repo / "projects/ROCKNIX/devices/SM8250/linux/dts"
    shutil.copytree(dts, source / "arch/arm64/boot/dts", dirs_exist_ok=True)
    custom_names = {
        "minimal-sleep": "sm8250-retroidpocket-rp5-minsleep4",
        "cpu-icc-off": "sm8250-retroidpocket-rp5-cpu-icc-off",
        "stock-pruned": "sm8250-retroidpocket-rp5-stock-pruned",
    }
    custom_name = custom_names.get(profile)
    matrix_names = []
    if custom_name:
        custom_dts = Path(__file__).resolve().parent / f"{custom_name}.dts"
        require(custom_dts.is_file(), f"Missing {profile} RP5 DTS")
        shutil.copyfile(custom_dts, source / "arch/arm64/boot/dts/qcom" / custom_dts.name)
        if profile == "stock-pruned":
            reference = Path(__file__).resolve().parent / "sm8250-retroidpocket-rp5-minsleep4.dts"
            require(reference.is_file(), "Missing minsleep4 reference DTS")
            shutil.copyfile(reference, source / "arch/arm64/boot/dts/qcom" / reference.name)
    elif profile == "reenable-matrix":
        root = Path(__file__).resolve().parent
        for filename in (
            "sm8250-retroidpocket-rp5-minsleep4.dts",
            "sm8250-retroidpocket-rp5-stock-pruned.dts",
        ):
            origin = root / filename
            require(origin.is_file(), f"Missing matrix reference DTS: {filename}")
            shutil.copyfile(origin, source / "arch/arm64/boot/dts/qcom" / filename)
        manifest_path = root / "reenable-matrix.json"
        generator = root / "generate-reenable-matrix.py"
        require(manifest_path.is_file() and generator.is_file(), "Missing matrix inputs")
        manifest = json.loads(manifest_path.read_text())
        matrix_names = [
            manifest["candidate_prefix"] + candidate["id"]
            for candidate in manifest["candidates"]
        ]
        subprocess.run(
            [sys.executable, str(generator), str(manifest_path),
             str(source / "arch/arm64/boot/dts/qcom")],
            check=True,
        )
    # Match the board used by the saved device baseline. This release predates
    # the separate Visionox DTS found in newer ROCKNIX revisions.
    makefile = source / "arch/arm64/boot/dts/qcom/Makefile"
    contents = makefile.read_text()
    names = ["sm8250-retroidpocket-rp5"]
    if custom_name:
        names.append(custom_name)
    if profile == "stock-pruned":
        names.append("sm8250-retroidpocket-rp5-minsleep4")
    elif profile == "reenable-matrix":
        names.extend([
            "sm8250-retroidpocket-rp5-minsleep4",
            "sm8250-retroidpocket-rp5-stock-pruned",
            *matrix_names,
        ])
    for name in names:
        require((source / f"arch/arm64/boot/dts/qcom/{name}.dts").is_file(), f"Missing {name} DTS")
        if f"{name}.dtb" not in contents:
            contents += f"\ndtb-$(CONFIG_ARCH_QCOM) += {name}.dtb\n"
    makefile.write_text(contents)
    return records


def main():
    require(len(sys.argv) == 6, "Usage: prepare.py RELEASE_TAR WORK SOURCE RECIPE OUTPUT")
    archive, work, source, repo, out = (Path(p).resolve() for p in sys.argv[1:])
    out.mkdir(parents=True, exist_ok=True)
    initramfs_sha = extract_stock(archive, work)
    profile = os.environ.get("ROCKNIX_PROFILE", "diagnostic")
    require(profile in (
        "diagnostic", "minimal-sleep", "cpu-icc-off", "stock-pruned",
        "reenable-matrix", "gmu-clock-reset", "gpu-rpmh-fix",
    ),
            f"Unsupported ROCKNIX_PROFILE: {profile}")
    records = apply_patches(
        repo, source,
        Path(__file__).resolve().parent.parent / "ufs-lane-clocks.patch",
        profile,
    )
    dt_only = profile in ("cpu-icc-off", "stock-pruned", "reenable-matrix")
    (out / "provenance.json").write_text(json.dumps({
        "rocknix_revision": REVISION, "stock_kernel_sha256": STOCK_KERNEL_SHA,
        "initramfs_sha256": initramfs_sha, "patches": records,
        "fix_commit": "f07317a8d57f382ec505597816271dd72ffa20c7",
        "gpu_rpmh_fix_commit": (
            "d9108bfdb746" if profile == "gpu-rpmh-fix" else None
        ),
        "display_fix": "Initialize DPU CRTC state before ROCKNIX resource-cleanup writes num_mixers",
        "baseline": "ROCKNIX 20260901 / Linux 7.2.0",
        "build": (
            "Native GitHub ARM runner; Device Tree only, stock kernel retained"
            if dt_only else
            "Native GitHub ARM runner, distro compiler; all modules rebuilt"
        ),
        "deployment": (
            "Not installed; intended only for the exact verified stock RP5 kernel"
            if dt_only else
            "Not installed; matching modules must be integrated with SYSTEM before boot"
        )
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
