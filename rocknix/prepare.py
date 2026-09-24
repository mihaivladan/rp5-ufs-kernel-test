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
    elif profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform"):
        rpmh_fix = Path(__file__).resolve().parent / "a6xx-stale-rpmh-votes.patch"
        require(rpmh_fix.is_file(), "Missing upstream stale RPMh vote fix")
        require(hashlib.sha256(rpmh_fix.read_bytes()).hexdigest() ==
                "6fa32840101b0b03554173ffb0a70b059f5acfbb81147f884d9cf12219c9d5ca",
                "GPU stale RPMh vote fix checksum mismatch")
        extra_patches.append(rpmh_fix)
    if profile in ("sleepstate-handshake", "slpi-integrated", "lpm-platform"):
        sleepstate_fix = Path(__file__).resolve().parent / "smp2p-sleepstate.patch"
        require(sleepstate_fix.is_file(), "Missing SMP2P sleep-state patch")
        require(hashlib.sha256(sleepstate_fix.read_bytes()).hexdigest() ==
                "f8d91b9aa78409f838dc4b38a1b25ce46ed0979e7daf9484d30c43e5ef8dd112",
                "SMP2P sleep-state patch checksum mismatch")
        extra_patches.append(sleepstate_fix)
    if profile in ("adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform"):
        no_auto = Path(__file__).resolve().parent / "sm8250-adsp-no-auto-boot.patch"
        require(no_auto.is_file(), "Missing SM8250 ADSP no-auto-boot patch")
        require(hashlib.sha256(no_auto.read_bytes()).hexdigest() ==
                "27a92a7e9bc3a37e1954efa97e518cb7808505cc04dc4eb246803ca07da61805",
                "SM8250 ADSP no-auto-boot patch checksum mismatch")
        extra_patches.append(no_auto)
    if profile == "adsp-before-bluetooth":
        qca_dependency = Path(__file__).resolve().parent / "qca-rproc-dependency.patch"
        require(qca_dependency.is_file(), "Missing QCA remoteproc dependency patch")
        require(hashlib.sha256(qca_dependency.read_bytes()).hexdigest() ==
                "818edb1cd2ddd0cb2a4323e4a183adf7514cdb275d76ea22cd7dee3fd4fdaec2",
                "QCA remoteproc dependency patch checksum mismatch")
        extra_patches.append(qca_dependency)
    if profile == "lpm-platform":
        lpm_fix = Path(__file__).resolve().parent / "qcom-lpm-platform-suspend.patch"
        require(lpm_fix.is_file(), "Missing exact-state platform suspend patch")
        require(hashlib.sha256(lpm_fix.read_bytes()).hexdigest() ==
                "74e062e6a4fba0ac780dd11bc462e9bf04abf2d9f0e300677a95bfee18dbdb8c",
                "Exact-state platform suspend patch checksum mismatch")
        extra_patches.append(lpm_fix)
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
    if profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform"):
        gmu_source = (source / "drivers/gpu/drm/msm/adreno/a6xx_gmu.c").read_text()
        require("if (!test_and_clear_bit(GMU_STATUS_FW_START, &gmu->status))" in gmu_source,
                "Corrected GMU firmware-start condition missing")
        require("gmu_write(gmu, REG_A6XX_GMU_CM3_SYSRESET, 1);" in gmu_source,
                "GMU CM3 reset before RPMh stop missing")
    if profile in ("sleepstate-handshake", "slpi-integrated", "lpm-platform"):
        sleepstate_source = (source / "drivers/soc/qcom/smp2p_sleepstate.c").read_text()
        require("case PM_SUSPEND_PREPARE:" in sleepstate_source and
                "case PM_POST_SUSPEND:" in sleepstate_source and
                "PROC_AWAKE_ID\t12" in sleepstate_source,
                "SMP2P sleep-state handshake implementation missing")
    if profile in ("adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform"):
        pas_source = (source / "drivers/remoteproc/qcom_q6v5_pas.c").read_text()
        start = pas_source.index("static const struct qcom_pas_data sm8250_adsp_resource = {")
        end = pas_source.index("\n};", start)
        block = pas_source[start:end]
        require(block.count(".auto_boot = false,") == 1 and ".auto_boot = true," not in block,
                "SM8250 ADSP auto-boot override missing")
    if profile == "adsp-before-bluetooth":
        hci_qca = (source / "drivers/bluetooth/hci_qca.c").read_text()
        require(hci_qca.count('of_property_read_u32(dev_of_node(dev), "qcom,rproc",') == 1 and
                hci_qca.count("err = rproc_boot(qcadev->rproc);") == 1 and
                hci_qca.count("devm_add_action_or_reset(dev, qca_release_rproc, qcadev)") == 1 and
                hci_qca.index("err = rproc_boot(qcadev->rproc);") <
                hci_qca.index("err = hci_uart_register_device(&qcadev->serdev_hu, &qca_proto);"),
                "QCA remoteproc dependency is missing or ordered after Bluetooth setup")
    if profile == "lpm-platform":
        lpm_source = (source / "drivers/soc/qcom/qcom_lpm_platform_suspend.c").read_text()
        require("#define CONSOLEOS_SM8250_SUSPEND_STATE\t0x4100c244" in lpm_source and
                "ret = cpu_pm_enter();" in lpm_source and
                "ret = psci_cpu_suspend_enter(consoleos_psci_state);" in lpm_source and
                "suspend_set_ops(&consoleos_suspend_ops);" in lpm_source,
                "Exact-state platform suspend implementation missing")
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
    elif profile in ("reenable-matrix", "sleepstate-handshake", "adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform"):
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
        if profile == "adsp-before-bluetooth":
            dependency_dts = root / "sm8250-retroidpocket-rp5-adsp-before-bluetooth.dts"
            require(dependency_dts.is_file(), "Missing RP5 ADSP-before-Bluetooth DTS")
            shutil.copyfile(
                dependency_dts,
                source / "arch/arm64/boot/dts/qcom" / dependency_dts.name,
            )
        elif profile == "sleepstate-handshake":
            sleepstate_dts = root / "sm8250-retroidpocket-rp5-adsp-sleepstate.dts"
            require(sleepstate_dts.is_file(), "Missing RP5 sleep-state DTS")
            shutil.copyfile(
                sleepstate_dts,
                source / "arch/arm64/boot/dts/qcom" / sleepstate_dts.name,
            )
        elif profile in ("slpi-integrated", "lpm-platform"):
            integrated_dts = root / "sm8250-retroidpocket-rp5-adsp-slpi-sleepstate.dts"
            require(integrated_dts.is_file(), "Missing integrated RP5 ADSP/SLPI DTS")
            shutil.copyfile(
                integrated_dts,
                source / "arch/arm64/boot/dts/qcom" / integrated_dts.name,
            )
            if profile == "lpm-platform":
                lpm_dts = root / "sm8250-retroidpocket-rp5-lpm-platform.dts"
                require(lpm_dts.is_file(), "Missing exact-state platform suspend DTS")
                shutil.copyfile(
                    lpm_dts,
                    source / "arch/arm64/boot/dts/qcom" / lpm_dts.name,
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
    elif profile == "sleepstate-handshake":
        names.append("sm8250-retroidpocket-rp5-adsp-sleepstate")
    elif profile == "adsp-no-auto-ab":
        names.append("sm8250-retroidpocket-rp5-reenable-display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-adsp")
    elif profile == "adsp-before-bluetooth":
        names.append("sm8250-retroidpocket-rp5-adsp-before-bluetooth")
    elif profile == "slpi-integrated":
        names.append("sm8250-retroidpocket-rp5-adsp-slpi-sleepstate")
    elif profile == "lpm-platform":
        names.extend([
            "sm8250-retroidpocket-rp5-adsp-slpi-sleepstate",
            "sm8250-retroidpocket-rp5-lpm-platform",
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
        "sleepstate-handshake", "adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform",
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
            "d9108bfdb746"
            if profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform") else None
        ),
        "sleepstate_handshake": (
            "Qualcomm downstream SMP2P awake bit 12 over the RP5 DSPS/SLPI channel"
            if profile in ("sleepstate-handshake", "slpi-integrated", "lpm-platform") else None
        ),
        "adsp_no_auto_boot": (
            "SM8250 ADSP remoteproc registered offline until explicit sysfs start"
            if profile in ("adsp-no-auto-ab", "adsp-before-bluetooth", "slpi-integrated", "lpm-platform") else None
        ),
        "bluetooth_adsp_dependency": (
            "QCA serdev synchronously boots and holds the DT-selected ADSP remoteproc before Bluetooth setup"
            if profile == "adsp-before-bluetooth" else None
        ),
        "platform_suspend": (
            "Memory-only suspend entry using exact Android SM8250 composite PSCI state 0x4100c244"
            if profile == "lpm-platform" else None
        ),
        "lpm_reference": (
            "LineageOS/android_kernel_oneplus_sm8250 ef098aedd975479e0aaf440bc14e8ba7c589c561"
            if profile == "lpm-platform" else None
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
