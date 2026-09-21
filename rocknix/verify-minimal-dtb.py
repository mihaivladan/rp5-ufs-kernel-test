#!/usr/bin/env python3
"""Reject a minsleep4 DTB unless its pruned slice and CPU ICC deletion match."""
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


def has_property(dtb, path, name):
    return fdtget(dtb, path, name, check=False).returncode == 0


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
        "vreg_l1c_1p8", "vreg_l8c_1p8",
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

    cpu_paths = ("/cpus/cpu@0",) + tuple(
        f"/cpus/cpu@{index}00" for index in range(1, 8)
    )
    checked_cpus = []
    for path in cpu_paths:
        fdtget(dtb, "-p", path)
        present = {
            name: has_property(dtb, path, name)
            for name in (
                "interconnects", "interconnect-names",
                "operating-points-v2", "qcom,freq-domain",
            )
        }
        checked_cpus.append((path, present))
        for name in ("interconnects", "interconnect-names"):
            if present[name]:
                failures.append(f"CPU ICC property remained: {path}/{name}")
        for name in ("operating-points-v2", "qcom,freq-domain"):
            if not present[name]:
                failures.append(f"required CPU frequency property missing: {path}/{name}")

    expected_opp_counts = {
        "cpu0_opp_table": 17,
        "cpu4_opp_table": 18,
        "cpu7_opp_table": 21,
    }
    checked_opps = []
    for symbol, expected_count in expected_opp_counts.items():
        path = symbol_path(dtb, symbol)
        children = [
            name for name in fdtget(dtb, "-l", path).stdout.splitlines()
            if name.startswith("opp-")
        ]
        if len(children) != expected_count:
            failures.append(
                f"{symbol} has {len(children)} OPPs, expected {expected_count}"
            )
        for child in children:
            child_path = f"{path}/{child}"
            if has_property(dtb, child_path, "opp-peak-kBps"):
                failures.append(f"CPU OPP bandwidth remained: {child_path}")
            if not has_property(dtb, child_path, "opp-hz"):
                failures.append(f"CPU OPP frequency missing: {child_path}")
        checked_opps.append((symbol, path, len(children)))

    if failures:
        raise SystemExit("Minimal DTB verification failed:\n  " + "\n  ".join(failures))

    print(f"Verified {len(checked_disabled)} explicitly disabled nodes:")
    for symbol, path, _ in checked_disabled:
        print(f"  disabled {symbol}: {path}")
    print(f"Verified {len(checked_retained)} retained core nodes:")
    for symbol, path, value in checked_retained:
        print(f"  retained {symbol}: {path} status={value or '<implicit okay>'}")
    print(f"Verified {len(checked_cpus)} CPUs retain frequency control with no ICC properties:")
    for path, _ in checked_cpus:
        print(f"  CPU ICC removed, OPP/frequency-domain retained: {path}")
    print(f"Verified CPU OPP bandwidth removal across {sum(x[2] for x in checked_opps)} OPPs:")
    for symbol, path, count in checked_opps:
        print(f"  {symbol}: {path}, {count} OPPs, no opp-peak-kBps")


if __name__ == "__main__":
    main()
