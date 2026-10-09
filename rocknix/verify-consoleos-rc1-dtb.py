#!/usr/bin/env python3
"""Prove that RC1 is the accepted full-stack DT plus one measured policy delta."""

import struct
import sys
from pathlib import Path


STATUS_NODES = {
    "/audio-codec", "/multi-ledl1", "/multi-ledl2", "/multi-ledl3",
    "/multi-ledl4", "/multi-ledr1", "/multi-ledr2", "/multi-ledr3",
    "/multi-ledr4", "/pwm-fan", "/sound", "/vdc-5v-regulator",
    "/vreg-fan-pwr-regulator", "/soc@0/codec@3240000",
    "/soc@0/codec@3370000",
    "/soc@0/display-subsystem@ae00000/displayport-controller@ae90000",
    "/soc@0/geniqup@8c0000/i2c@884000",
    "/soc@0/geniqup@9c0000/i2c@98c000",
    "/soc@0/geniqup@9c0000/i2c@994000",
    "/soc@0/geniqup@ac0000", "/soc@0/geniqup@ac0000/i2c@a94000",
    "/soc@0/phy@88e3000", "/soc@0/phy@88e8000",
    "/soc@0/pinctrl@33c0000", "/soc@0/pmu@9091000",
    "/soc@0/pmu@90b6400", "/soc@0/remoteproc@17300000",
    "/soc@0/rsc@18200000/regulators-1/ldo1", "/soc@0/rxmacro@3200000",
    "/soc@0/soundwire@3210000", "/soc@0/soundwire@3230000",
    "/soc@0/soundwire@3250000", "/soc@0/spmi@c440000/pmic@2/typec@1500",
    "/soc@0/spmi@c440000/pmic@2/usb-vbus-regulator@1100",
    "/soc@0/spmi@c440000/pmic@3/haptics@c000",
    "/soc@0/spmi@c440000/pmic@5/pwm", "/soc@0/txmacro@3220000",
    "/soc@0/usb@a6f8800", "/soc@0/usb@a6f8800/usb@a600000",
}
PCIE = "/soc@0/pcie@1c00000"
PWM_FAN = "/pwm-fan"
MCU_ALWAYS_ON = "/vreg-mcu-3v3-regulator/regulator-always-on"
PCIE_SLEEP = "/soc@0/pinctrl@f100000/pcie0-sleep-state"
PCIE_SLEEP_CLKREQ = f"{PCIE_SLEEP}/clkreq-pins"
PANEL = "/soc@0/display-subsystem@ae00000/dsi@ae94000/panel@0"
PANEL_MIPI_VDD = "/consoleos-panel-mipi-vdd-regulator"
TYPEC_MUX_1C = "/soc@0/geniqup@8c0000/i2c@884000/typec-mux@1c"
STATIC_EXPECTED = {f"{node}/status" for node in STATUS_NODES} | {
    f"{PCIE}/qcom,drv-supported", f"{PCIE}/qcom,drv-dev-id",
    f"{PCIE}/qcom,drv-l1ss-timeout-us", f"{PCIE}/pinctrl-names",
    f"{PCIE}/pinctrl-1", f"{PCIE}/interconnects",
    f"{PCIE}/interconnect-names", f"{PWM_FAN}/fan-startup-percent",
    MCU_ALWAYS_ON,
    f"{PCIE_SLEEP}/phandle", f"{PCIE_SLEEP_CLKREQ}/pins",
    f"{PCIE_SLEEP_CLKREQ}/function", f"{PCIE_SLEEP_CLKREQ}/drive-strength",
    f"{PCIE_SLEEP_CLKREQ}/bias-pull-up",
    f"{PANEL}/mipi-vdd-supply", f"{TYPEC_MUX_1C}/status",
    f"{PANEL_MIPI_VDD}/compatible", f"{PANEL_MIPI_VDD}/regulator-name",
    f"{PANEL_MIPI_VDD}/regulator-boot-on", f"{PANEL_MIPI_VDD}/gpio",
    f"{PANEL_MIPI_VDD}/enable-active-high", f"{PANEL_MIPI_VDD}/phandle",
}


def parse(path: Path) -> dict[str, bytes]:
    blob = path.read_bytes()
    magic, total, off_struct, off_strings, _, version, _, _, strings_len, struct_len = \
        struct.unpack_from(">10I", blob)
    if magic != 0xD00DFEED or total != len(blob) or version != 17:
        raise ValueError(f"unexpected FDT header: {path}")
    strings = blob[off_strings:off_strings + strings_len]
    cursor, end, stack, properties = off_struct, off_struct + struct_len, [], {}
    while cursor < end:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:
            nul = blob.index(b"\0", cursor, end)
            stack.append(blob[cursor:nul].decode())
            cursor = (nul + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            name_end = strings.index(b"\0", name_offset)
            name = strings[name_offset:name_end].decode()
            node = "/" + "/".join(part for part in stack if part)
            properties[f"{node}/{name}"] = blob[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            break
        else:
            raise ValueError(f"unexpected FDT token {token}: {path}")
    return properties


def status(value: bytes) -> bytes:
    return value.split(b"\0", 1)[0]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-consoleos-rc1-dtb.py PHASE4A_BASE RC1")
    base, candidate = parse(Path(sys.argv[1])), parse(Path(sys.argv[2]))
    gamepad_compat = b"retroid,retroid-pocket-gamepad\0"
    gamepad_nodes = {
        key.removesuffix("/compatible")
        for key, value in base.items()
        if key.endswith("/compatible") and value == gamepad_compat
    }
    if len(gamepad_nodes) != 1:
        raise SystemExit(f"expected one Retroid gamepad node, got {sorted(gamepad_nodes)}")
    gamepad = gamepad_nodes.pop()
    expected = STATIC_EXPECTED | {f"{gamepad}/vdd-supply"}
    fixed_sleep_phandle = struct.pack(">I", 0x10000)
    if any(key.endswith("/phandle") and value == fixed_sleep_phandle
           for key, value in base.items()):
        raise SystemExit("reserved PCIe sleep phandle collides with base tree")
    changed = {key for key in base.keys() | candidate.keys()
               if base.get(key) != candidate.get(key)}
    if changed != expected:
        missing, extra = expected - changed, changed - expected
        raise SystemExit(f"wrong RC1 delta; missing={sorted(missing)} extra={sorted(extra)}")
    for node in STATUS_NODES:
        key = f"{node}/status"
        if status(base[key]) != b"disabled" or status(candidate[key]) != b"okay":
            raise SystemExit(f"wrong status transition: {node}")
    if candidate[f"{PCIE}/qcom,drv-supported"] != b"":
        raise SystemExit("qcom,drv-supported payload changed")
    if candidate[f"{PCIE}/qcom,drv-dev-id"] != struct.pack(">I", 0):
        raise SystemExit("wrong PCIe DRV device ID")
    if candidate[f"{PCIE}/qcom,drv-l1ss-timeout-us"] != struct.pack(">I", 10000):
        raise SystemExit("wrong PCIe DRV L1SS timeout")
    if candidate[f"{PCIE}/interconnect-names"] != b"pcie-mem\0cpu-pcie\0":
        raise SystemExit("wrong PCIe interconnect names")
    providers = (
        ("/soc@0/interconnect@1700000", 6, 7),
        ("/soc@0/interconnect@163d000", 1, 7),
        ("/soc@0/interconnect@9100000", 2, 3),
        ("/soc@0/interconnect@1620000", 14, 3),
    )
    expected_icc = b"".join(
        candidate[f"{node}/phandle"] + struct.pack(">II", endpoint, tag)
        for node, endpoint, tag in providers
    )
    if candidate[f"{PCIE}/interconnects"] != expected_icc:
        raise SystemExit("wrong PCIe interconnect providers, endpoints or tags")
    if candidate[f"{PWM_FAN}/fan-startup-percent"] != struct.pack(">I", 0):
        raise SystemExit("RP5 fan does not request a silent kernel startup")
    if status(candidate[f"{TYPEC_MUX_1C}/status"]) != b"disabled":
        raise SystemExit("misidentified 15-001c node is not statically disabled")
    if candidate[f"{PANEL_MIPI_VDD}/compatible"] != b"regulator-fixed\0" or \
       candidate[f"{PANEL_MIPI_VDD}/regulator-name"] != \
       b"consoleos_panel_mipi_vdd\0":
        raise SystemExit("GPIO28 panel MIPI-VDD regulator identity mismatch")
    if candidate[f"{PANEL_MIPI_VDD}/regulator-boot-on"] != b"" or \
       candidate[f"{PANEL_MIPI_VDD}/enable-active-high"] != b"":
        raise SystemExit("GPIO28 panel MIPI-VDD regulator policy mismatch")
    tlmm_phandle = candidate["/soc@0/pinctrl@f100000/phandle"]
    if candidate[f"{PANEL_MIPI_VDD}/gpio"] != \
       tlmm_phandle + struct.pack(">II", 28, 0):
        raise SystemExit("panel MIPI-VDD regulator does not own active-high GPIO28")
    if candidate[f"{PANEL}/mipi-vdd-supply"] != \
       candidate[f"{PANEL_MIPI_VDD}/phandle"]:
        raise SystemExit("CH13726A panel is not linked to the GPIO28 MIPI-VDD supply")
    if candidate[f"{PCIE}/pinctrl-names"] != b"default\0sleep\0":
        raise SystemExit("wrong PCIe pinctrl state names")
    sleep_phandle = candidate[f"{PCIE_SLEEP}/phandle"]
    if sleep_phandle != fixed_sleep_phandle or \
       candidate[f"{PCIE}/pinctrl-1"] != sleep_phandle:
        raise SystemExit("PCIe sleep state phandle mismatch")
    expected_sleep = {
        f"{PCIE_SLEEP_CLKREQ}/pins": b"gpio80\0",
        f"{PCIE_SLEEP_CLKREQ}/function": b"gpio\0",
        f"{PCIE_SLEEP_CLKREQ}/drive-strength": struct.pack(">I", 2),
        f"{PCIE_SLEEP_CLKREQ}/bias-pull-up": b"",
    }
    for key, value in expected_sleep.items():
        if candidate[key] != value:
            raise SystemExit(f"wrong PCIe sleep property: {key}")
    default_clkreq = "/soc@0/pinctrl@f100000/pcie0-default-state/clkreq-pins/function"
    if candidate[default_clkreq] != b"pci_e0\0":
        raise SystemExit("PCIe default CLKREQ mux was altered")
    if MCU_ALWAYS_ON not in base or MCU_ALWAYS_ON in candidate:
        raise SystemExit("MCU/RGB rail was not changed from always-on to controllable")
    rail_phandle = candidate["/vreg-mcu-3v3-regulator/phandle"]
    if candidate[f"{gamepad}/vdd-supply"] != rail_phandle:
        raise SystemExit("gamepad is not a consumer of the shared MCU/RGB rail")
    for node in (
        "/soc@0/crypto@1dfa000", "/soc@0/remoteproc@5c00000",
        "/soc@0/remoteproc@8300000", "/soc@0/video-codec@aa00000",
    ):
        if status(candidate[f"{node}/status"]) != b"disabled":
            raise SystemExit(f"unvalidated subsystem enabled: {node}")
    print("VERIFIED: product stack plus MCU/RGB PM, GPIO80, GPIO28 panel supply and disabled 15-001c")


if __name__ == "__main__":
    main()
