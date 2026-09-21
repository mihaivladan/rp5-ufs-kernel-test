#!/usr/bin/env python3
"""Reject a minsleep2 DTB unless its disabled and retained slices match."""
from pathlib import Path
import subprocess
import sys


def fdtget(dtb, *args, check=True):
    result = subprocess.run(
        ["fdtget", str(dtb), *args], text=True, capture_output=True
    )
    if check and result.returncode:
        raise SystemExit(
            f"fdtget {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result


def symbol_path(dtb, symbol):
    path = fdtget(dtb, "/__symbols__", symbol).stdout.strip()
    if not path.startswith("/"):
        raise SystemExit(f"Invalid path for symbol {symbol}: {path!r}")
    fdtget(dtb, "-p", path)
    return path


def status(dtb, path):
    result = fdtget(dtb, path, "status", check=False)
    if result.returncode:
        return None
    return result.stdout.strip()


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-minimal-dtb.py DTB")
    dtb = Path(sys.argv[1]).resolve()
    if not dtb.is_file():
        raise SystemExit(f"Missing DTB: {dtb}")

    disabled_symbols = (
        "framebuffer", "mdss", "mdss_dp", "mdss_dsi0", "mdss_dsi0_phy",
        "dispcc", "gpu", "gmu", "gpucc", "adreno_smmu", "venus",
        "videocc", "camcc", "pcie0", "pcie0_phy", "uart6", "ufs_mem_hc",
        "ufs_mem_phy", "usb_1", "usb_1_dwc3", "usb_1_hsphy",
        "usb_1_qmpphy", "pm8150b_typec", "pm8150b_vbus", "i2c15",
        "crypto", "adsp", "cdsp", "slpi", "sound", "wcd938x", "rxmacro",
        "txmacro", "wsamacro", "swr0", "swr1", "swr2", "i2c3", "i2c5",
        "i2c13", "uart12", "uart16", "qupv3_id_0", "qupv3_id_1",
        "qupv3_id_2", "fan", "pm8150b_haptics", "pm8150l_lpg", "vbat",
        "vdc_3v3", "vdc_5v", "vdda_panel", "vreg_s4a_1p8",
        "vreg_fan_pwr", "vreg_mcu_3v3",
    )
    disabled_paths = (
        "/qca6390-pmu",
        "/soc@0/pmu@9091000", "/soc@0/pmu@90b6400",
        "/multi-ledr1", "/multi-ledl1", "/multi-ledr2", "/multi-ledl2",
        "/multi-ledr3", "/multi-ledl3", "/multi-ledr4", "/multi-ledl4",
    )
    retained_symbols = (
        "sdhc_2", "pon_pwrkey", "apps_rsc", "rpmhcc", "rpmhpd",
        "aoss_qmp", "gcc", "qup_virt", "config_noc", "system_noc",
        "mc_virt", "aggre1_noc", "aggre2_noc", "mmss_noc", "gem_noc",
    )

    failures = []
    checked_disabled = []
    for symbol in disabled_symbols:
        path = symbol_path(dtb, symbol)
        value = status(dtb, path)
        checked_disabled.append((symbol, path, value))
        if value != "disabled":
            failures.append(f"{symbol} ({path}) status is {value!r}")
    for path in disabled_paths:
        fdtget(dtb, "-p", path)
        value = status(dtb, path)
        checked_disabled.append((path, path, value))
        if value != "disabled":
            failures.append(f"{path} status is {value!r}")

    checked_retained = []
    for symbol in retained_symbols:
        path = symbol_path(dtb, symbol)
        value = status(dtb, path)
        checked_retained.append((symbol, path, value))
        if value == "disabled":
            failures.append(f"retained {symbol} ({path}) is disabled")

    if failures:
        raise SystemExit("Minimal DTB verification failed:\n  " + "\n  ".join(failures))

    print(f"Verified {len(checked_disabled)} explicitly disabled nodes:")
    for symbol, path, _ in checked_disabled:
        print(f"  disabled {symbol}: {path}")
    print(f"Verified {len(checked_retained)} retained core nodes:")
    for symbol, path, value in checked_retained:
        print(f"  retained {symbol}: {path} status={value or '<implicit okay>'}")


if __name__ == "__main__":
    main()
