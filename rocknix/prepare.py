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
    elif profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform"):
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
    if profile in ("adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform"):
        no_auto = Path(__file__).resolve().parent / "sm8250-adsp-no-auto-boot.patch"
        require(no_auto.is_file(), "Missing SM8250 ADSP no-auto-boot patch")
        require(hashlib.sha256(no_auto.read_bytes()).hexdigest() ==
                "27a92a7e9bc3a37e1954efa97e518cb7808505cc04dc4eb246803ca07da61805",
                "SM8250 ADSP no-auto-boot patch checksum mismatch")
        extra_patches.append(no_auto)
    if profile in ("lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        devote_fix = Path(__file__).resolve().parent / "q6afe-lpass-hw-vote-handle.patch"
        require(devote_fix.is_file(), "Missing Q6AFE LPASS vote-handle patch")
        require(hashlib.sha256(devote_fix.read_bytes()).hexdigest() ==
                "3104d397cd7a9be8e09c5c66e12411e9d6ea9fe6c1fbef6016f9cf6586076238",
                "Q6AFE LPASS vote-handle patch checksum mismatch")
        extra_patches.append(devote_fix)
    if profile in ("lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        macro_pm = Path(__file__).resolve().parent / "lpass-macro-pm-clock-backport.patch"
        require(macro_pm.is_file(), "Missing upstream LPASS macro PM-clock backport")
        require(hashlib.sha256(macro_pm.read_bytes()).hexdigest() ==
                "83d363a201162f24325aca249bd9f4bff58765bdb0da5f1a0b0636ea4c8c29ab",
                "LPASS macro PM-clock backport checksum mismatch")
        extra_patches.append(macro_pm)
    if profile in ("pcie-drv-handoff", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        drv_fix = Path(__file__).resolve().parent / "qcom-pcie-drv-handoff.patch"
        require(drv_fix.is_file(), "Missing Qualcomm PCIe DRV handoff patch")
        require(hashlib.sha256(drv_fix.read_bytes()).hexdigest() ==
                "dd753274154ed05388a367087dce9c27cc3da0e01390c785640b1cd4af2ddfdd",
                "Qualcomm PCIe DRV handoff patch checksum mismatch")
        extra_patches.append(drv_fix)
    if profile == "consoleos-rc1":
        offline_fix = Path(__file__).resolve().parent / "qcom-pcie-drv-offline-suspend.patch"
        pinctrl_fix = Path(__file__).resolve().parent / "qcom-pcie-offline-pinctrl.patch"
        require(offline_fix.is_file() and pinctrl_fix.is_file(),
                "Missing Qualcomm PCIe offline pinctrl patches")
        require(hashlib.sha256(offline_fix.read_bytes()).hexdigest() ==
                "f353f171ca02c5206e6e2db39c38709ea3bf0a342bfc902c08b3f0e0fd93607a",
                "Qualcomm PCIe offline-suspend patch checksum mismatch")
        require(hashlib.sha256(pinctrl_fix.read_bytes()).hexdigest() ==
                "2c57942a2912ac64460386283b926f5cd7a245770b14f4c073b2d7a178bdce2f",
                "Qualcomm PCIe offline pinctrl patch checksum mismatch")
        extra_patches.extend((offline_fix, pinctrl_fix))
    if profile in ("fg-coulomb-counter", "consoleos-rc1"):
        fg_counter = Path(__file__).resolve().parent / "qcom-fg-gen4-coulomb-counter.patch"
        require(fg_counter.is_file(), "Missing PM8150B Gen4 coulomb-counter patch")
        require(hashlib.sha256(fg_counter.read_bytes()).hexdigest() ==
                "87bd94920f1f24722cb55a8c7bb2068599103469797188cd284c1ad28b6e50e8",
                "PM8150B Gen4 coulomb-counter patch checksum mismatch")
        extra_patches.append(fg_counter)
    if profile == "consoleos-rc1":
        gamepad_remove = Path(__file__).resolve().parent / "retroid-gamepad-remove.patch"
        require(gamepad_remove.is_file(), "Missing Retroid gamepad cleanup patch")
        require(hashlib.sha256(gamepad_remove.read_bytes()).hexdigest() ==
                "9201056b19b2bd62b1827e91351bdf4369259b4894aadc609b5a73c832e09919",
                "Retroid gamepad cleanup patch checksum mismatch")
        extra_patches.append(gamepad_remove)
        rpmh_suspend = Path(__file__).resolve().parent / "rpmh-regulator-suspend-state.patch"
        regulator_s2idle = Path(__file__).resolve().parent / "regulator-core-s2idle-state-mem.patch"
        rpmh_trace = Path(__file__).resolve().parent / "rpmh-regulator-suspend-trace.patch"
        require(rpmh_suspend.is_file() and regulator_s2idle.is_file() and rpmh_trace.is_file(),
                "Missing RC3 RPMh regulator suspend patches")
        require(hashlib.sha256(rpmh_suspend.read_bytes()).hexdigest() ==
                "71e0d61fda70fe3f2a7e3ebb94fa89b56434e89d9b7ee03848b9f3cabcfb38b9",
                "RPMh regulator suspend-state patch checksum mismatch")
        require(hashlib.sha256(regulator_s2idle.read_bytes()).hexdigest() ==
                "86ccb3f75b89920e289f16609eca288eb4bb3d6f93ffea7a267631280379037c",
                "Regulator s2idle state-mem patch checksum mismatch")
        require(hashlib.sha256(rpmh_trace.read_bytes()).hexdigest() ==
                "06c5d49db2dc5ac27fa4fc04700df94fb6751c0678ed77c07c275c2d7b8476e6",
                "RPMh regulator suspend trace patch checksum mismatch")
        extra_patches.extend((rpmh_suspend, regulator_s2idle, rpmh_trace))
        pdphy_gating = Path(__file__).resolve().parent / "qcom-pmic-typec-pdphy-attach-gating.patch"
        require(pdphy_gating.is_file(), "Missing PM8150B PD-PHY attach-gating patch")
        require(hashlib.sha256(pdphy_gating.read_bytes()).hexdigest() ==
                "a5eb215c6a2477407882e345bfff4b0a7f5864196c7773456507fd7ad4de8c59",
                "PM8150B PD-PHY attach-gating patch checksum mismatch")
        extra_patches.append(pdphy_gating)
        orderly_qca = Path(__file__).resolve().parent / "qca6390-orderly-poweroff.patch"
        require(orderly_qca.is_file(), "Missing guarded QCA6390 orderly power-off patch")
        require(hashlib.sha256(orderly_qca.read_bytes()).hexdigest() ==
                "8fdacde833ac82356c3ffbe8a564beaf23bea79469c5418c52e5ea3225c91384",
                "QCA6390 orderly power-off patch checksum mismatch")
        extra_patches.append(orderly_qca)
        orderly_icc = Path(__file__).resolve().parent / "qcom-pcie-orderly-icc-release.patch"
        require(orderly_icc.is_file(), "Missing orderly PCIe ICC release patch")
        require(hashlib.sha256(orderly_icc.read_bytes()).hexdigest() ==
                "abdbd431d328d0efcd1acc29aff128f5b8a67163ba51cb1838fedb491bbcf961",
                "Orderly PCIe ICC release patch checksum mismatch")
        extra_patches.append(orderly_icc)
        early_orderly = Path(__file__).resolve().parent / "qca6390-early-orderly-poweroff.patch"
        require(early_orderly.is_file(),
                "Missing QCA6390 early orderly power-off patch")
        require(hashlib.sha256(early_orderly.read_bytes()).hexdigest() ==
                "11506019a79f93b3afe0b63011ad28e5c019edf23669b33dd0dcc5753b1b19d5",
                "QCA6390 early orderly power-off patch checksum mismatch")
        extra_patches.append(early_orderly)
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
    if profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform"):
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
    if profile in ("adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform"):
        pas_source = (source / "drivers/remoteproc/qcom_q6v5_pas.c").read_text()
        start = pas_source.index("static const struct qcom_pas_data sm8250_adsp_resource = {")
        end = pas_source.index("\n};", start)
        block = pas_source[start:end]
        require(block.count(".auto_boot = false,") == 1 and ".auto_boot = true," not in block,
                "SM8250 ADSP auto-boot override missing")
    if profile in ("lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        afe_source = (source / "sound/soc/qcom/qdsp6/q6afe.c").read_text()
        require("afe->lpass_hw_client_handle = *(const u32 *)data->payload;" in afe_source and
                "refusing LPASS HW devote with zero handle" in afe_source and
                "LPASS HW devote complete: block=%u handle=%u" in afe_source,
                "Q6AFE LPASS vote-handle repair missing")
    if profile in ("lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        for path, name in {
            "sound/soc/codecs/lpass-wsa-macro.c": "wsa",
            "sound/soc/codecs/lpass-va-macro.c": "va",
            "sound/soc/codecs/lpass-rx-macro.c": "rx",
            "sound/soc/codecs/lpass-tx-macro.c": "tx",
        }.items():
            macro_source = (source / path).read_text()
            required = (
                "#include <linux/pm_clock.h>",
                "devm_pm_clk_create(dev)",
                "of_pm_clk_add_clks(dev)",
                "pm_runtime_set_autosuspend_delay(dev, 100)",
                "pm_clk_suspend(dev)",
                "pm_clk_resume(dev)",
            )
            require(all(marker in macro_source for marker in required),
                    f"LPASS {name.upper()} PM-clock conversion missing")
            require(f"clk_prepare_enable({name}->macro)" not in macro_source and
                    f"clk_prepare_enable({name}->dcodec)" not in macro_source,
                    f"LPASS {name.upper()} still directly enables vote clocks")
    if profile == "consoleos-rc1":
        pcie_source = (source / "drivers/pci/controller/dwc/pcie-qcom.c").read_text()
        required_pcie_offline = (
            "bool drv_offline;",
            "static bool qcom_pcie_has_downstream_device(",
            "pinctrl_pm_select_sleep_state(dev);",
            "pinctrl_pm_select_default_state(dev);",
            "PCIe RC%u offline sleep pins selected",
            "PCIe RC%u offline default pins restored",
            "PCIe RC%u endpoint absent; skipping ADSP handoff",
            "PCIe RC%u offline suspend completed",
        )
        require(all(marker in pcie_source for marker in required_pcie_offline),
                "Qualcomm PCIe offline pinctrl implementation markers missing")
        gamepad = (source / "drivers/input/joystick/retroid.c").read_text()
        require("static void gamepad_mcu_uart_remove(struct serdev_device *serdev)" in gamepad and
                "device_remove_file(&gamepad_dev->platform_dev->dev," in gamepad and
                "platform_device_unregister(gamepad_dev->platform_dev);" in gamepad and
                ".remove = gamepad_mcu_uart_remove," in gamepad,
                "Retroid gamepad unbind cleanup missing")
        rpmh_regulator = (source / "drivers/regulator/qcom-rpmh-regulator.c").read_text()
        regulator_core = (source / "drivers/regulator/core.c").read_text()
        required_rpmh = (
            "static int rpmh_regulator_set_suspend_enable(",
            "static int rpmh_regulator_vrm_set_suspend_mode(",
            "static int rpmh_regulator_resume(",
            ".set_suspend_enable\t= rpmh_regulator_set_suspend_enable,",
            ".set_suspend_mode\t= rpmh_regulator_vrm_set_suspend_mode,",
            ".resume\t\t\t= rpmh_regulator_resume,",
            "return rpmh_write(vreg->dev, RPMH_SLEEP_STATE, cmd, 1);",
            "consoleos-rpmh-policy: regulator=%s addr=%#x sleep_mode=%d wake_mode=%d",
        )
        require(all(marker in rpmh_regulator for marker in required_rpmh),
                "RC3 RPMh regulator SLEEP/WAKE implementation markers missing")
        require("case PM_SUSPEND_TO_IDLE:\n\tcase PM_SUSPEND_MEM:" in regulator_core,
                "RC3 s2idle is not mapped to regulator state_mem")
        pdphy = (source / "drivers/usb/typec/tcpm/qcom/qcom_pmic_typec_pdphy.c").read_text()
        required_pdphy = (
            "qcom_pmic_typec_pdphy_power_on(",
            "qcom_pmic_typec_pdphy_power_off(",
            "qcom_pmic_typec_port_is_attached(tcpm);",
            "PD PHY left off until Type-C attachment",
            "disable_irq_nosync(pmic_typec_pdphy->irq_data[i].irq);",
            "regulator_set_voltage(pmic_typec_pdphy->vdd_pdphy,",
            "regulator_set_load(pmic_typec_pdphy->vdd_pdphy, 0);",
        )
        require(all(marker in pdphy for marker in required_pdphy),
                "PM8150B PD-PHY attach-gating markers missing")
        start = pdphy.index("qcom_pmic_typec_pdphy_start(")
        stop = pdphy.index("qcom_pmic_typec_pdphy_stop(", start)
        require("regulator_enable(" not in pdphy[start:stop],
                "PM8150B PD PHY is still powered at driver start")
        tx_signal = pdphy.index("qcom_pmic_typec_pdphy_pd_transmit_signal(")
        tx_payload = pdphy.index("qcom_pmic_typec_pdphy_pd_transmit_payload(", tx_signal)
        receive = pdphy.index("qcom_pmic_typec_pdphy_pd_receive(", tx_payload)
        isr = pdphy.index("qcom_pmic_typec_pdphy_isr(", receive)
        require("state_lock" not in pdphy[tx_signal:tx_payload],
                "PD signal transmit has an unbalanced state mutex")
        require("state_lock" not in pdphy[receive:isr],
                "PD receive has an unbalanced state mutex")
        port = (source / "drivers/usb/typec/tcpm/qcom/qcom_pmic_typec_port.c").read_text()
        require("int qcom_pmic_typec_port_is_attached(struct pmic_typec *tcpm)" in port and
                "return !!(misc & CC_ATTACHED);" in port,
                "PM8150B physical CC-attachment guard missing")
        ath11k_pci = (source / "drivers/net/wireless/ath/ath11k/pci.c").read_text()
        qcom_pcie = (source / "drivers/pci/controller/dwc/pcie-qcom.c").read_text()
        require("module_param(orderly_qca_poweroff, bool, 0644);" in ath11k_pci and
                "QFPROM_PWR_CTRL_SHUTDOWN_EN_MASK" in ath11k_pci and
                "qcom_pcie_orderly_poweroff_prepare(ab_pci->pdev)" in ath11k_pci and
                "orderly_early_down" in ath11k_pci and
                "qcom_pcie_orderly_poweroff_arm(ab_pci->pdev)" in ath11k_pci and
                "MHI_CHANNEL_SUSPEND_RETAINED" in ath11k_pci,
                "Guarded ath11k orderly power-off markers missing")
        qrtr = (source / "net/qrtr/mhi.c").read_text()
        require("retaining IPCR channels for orderly QCA firmware stop" in qrtr and
                "MHI_CHANNEL_SUSPEND_RESET" in qrtr,
                "Orderly QCA QRTR channel-lifetime markers missing")
        require("orderly QCA power-off: L23 acknowledged" in qcom_pcie and
                "pre-shutdown ADSP handoff/reclaim complete" in qcom_pcie and
                "qcom_pcie_orderly_poweroff_arm" in qcom_pcie and
                "pci_pwrctrl_power_off_devices(pci->dev);" in qcom_pcie and
                "orderly QCA power-off: root complex suspended, ICC votes zero" in qcom_pcie and
                "orderly QCA power-off: ICC votes, root complex and endpoint rails restored" in qcom_pcie,
                "Guarded Qualcomm PCIe orderly power-off markers missing")
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
    elif profile in ("reenable-matrix", "sleepstate-handshake", "adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform"):
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
        if profile == "sleepstate-handshake":
            sleepstate_dts = root / "sm8250-retroidpocket-rp5-adsp-sleepstate.dts"
            require(sleepstate_dts.is_file(), "Missing RP5 sleep-state DTS")
            shutil.copyfile(
                sleepstate_dts,
                source / "arch/arm64/boot/dts/qcom" / sleepstate_dts.name,
            )
        elif profile in ("pcie-drv-handoff", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
            drv_dts = root / "sm8250-retroidpocket-rp5-phase4a-wireless-adsp-pcie-drv.dts"
            require(drv_dts.is_file(), "Missing RP5 PCIe DRV handoff DTS")
            shutil.copyfile(
                drv_dts,
                source / "arch/arm64/boot/dts/qcom" / drv_dts.name,
            )
            if profile == "consoleos-rc1":
                rc_dts = root / "sm8250-retroidpocket-rp5-consoleos-rc1.dts"
                require(rc_dts.is_file(), "Missing ConsoleOS RC1 DTS")
                shutil.copyfile(
                    rc_dts,
                    source / "arch/arm64/boot/dts/qcom" / rc_dts.name,
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
    elif profile in ("adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock"):
        names.append("sm8250-retroidpocket-rp5-reenable-display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-adsp")
    elif profile in ("pcie-drv-handoff", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1"):
        names.extend([
            "sm8250-retroidpocket-rp5-reenable-display-gpu-gamepad-active-only-ufs-wireless",
            "sm8250-retroidpocket-rp5-phase4a-wireless-adsp-pcie-drv",
        ])
        if profile == "consoleos-rc1":
            names.append("sm8250-retroidpocket-rp5-consoleos-rc1")
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
        "sleepstate-handshake", "adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform",
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
            if profile in ("gpu-rpmh-fix", "sleepstate-handshake", "adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform") else None
        ),
        "sleepstate_handshake": (
            "Qualcomm downstream SMP2P awake bit 12 over the RP5 DSPS/SLPI channel"
            if profile in ("sleepstate-handshake", "slpi-integrated", "lpm-platform") else None
        ),
        "adsp_no_auto_boot": (
            "SM8250 ADSP remoteproc registered offline until explicit sysfs start"
            if profile in ("adsp-no-auto-ab", "lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1", "pcie-drv-handoff", "slpi-integrated", "lpm-platform") else None
        ),
        "lpass_devote_fix": (
            "Preserve DSP LPASS hardware-vote handles and synchronously validate DEVOTE"
            if profile in ("lpass-devote-fix", "lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1") else None
        ),
        "lpass_macro_pm_clock_backport": (
            [
                "cd054a6e272caa97ab808ef6f5588749a1429108",
                "eb667d0fbdd38d5a800b9e7aafc9a6c14530b9bf",
                "b9b23e72abef91ab4689f1467cefc2517042ab26",
                "b05482e7ce1b110f86b08a99768ac41e4c9e4dfa",
            ] if profile in ("lpass-pm-clock", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1") else None
        ),
        "pcie_drv_handoff": (
            "ACK-validated ADSP pcie_drv ownership transfer around SM8250 PCIe RC0 resource release"
            if profile in ("pcie-drv-handoff", "audio-pcie-integration", "fg-coulomb-counter", "consoleos-rc1") else None
        ),
        "pm8150b_gen4_coulomb_diagnostics": (
            "Revision-selected raw CC_SOC/CC_SOC_SW/BATT_SOC plus derived microamp-hours"
            if profile in ("fg-coulomb-counter", "consoleos-rc1") else None
        ),
        "consoleos_rc1_policy": (
            "Accepted Phase 4AT full stack; shared MCU/RGB/gamepad rail controllable; "
            "gamepad unbind cleanup; failed/no-effect RPMh, endpoint-off and exact-state "
            "diagnostics excluded"
            if profile == "consoleos-rc1" else None
        ),
        "pm8150b_pdphy_attach_gating": (
            "Keep CC detection active; power PD PHY and L2A only during an attached session"
            if profile == "consoleos-rc1" else None
        ),
        "qca6390_orderly_poweroff": (
            "Opt-in firmware OFF, WLAON shutdown, MHI down, PCIe L23, true-zero ICC release, host resources and endpoint rails"
            if profile == "consoleos-rc1" else None
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
