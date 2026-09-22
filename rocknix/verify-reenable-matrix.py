#!/usr/bin/env python3
"""Verify exact semantic deltas for every RP5 subsystem re-enable candidate."""

import json
import struct
import sys
from pathlib import Path


ACTIVE_ONLY_CANDIDATES = {
    "display-gpu-gamepad-active-only",
    "display-gpu-gamepad-active-only-ufs",
    "display-gpu-gamepad-active-only-ufs-wireless",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-adsp-lpass",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-lpass",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec-audio",
}
UART6_ACTIVE_ONLY_CANDIDATES = {
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec-audio",
}


def parse_fdt(path: Path):
    blob = path.read_bytes()
    if len(blob) < 40:
        raise ValueError(f"Truncated DTB: {path}")
    header = struct.unpack_from(">10I", blob)
    magic, size, off_struct, off_strings, _, version, _, _, strings_len, struct_len = header
    if magic != 0xD00DFEED or size != len(blob) or version != 17:
        raise ValueError(f"Unexpected FDT header: {path}")
    strings = blob[off_strings:off_strings + strings_len]
    cursor, end_struct, stack = off_struct, off_struct + struct_len, []
    nodes, properties = set(), {}
    while cursor < end_struct:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:
            end = blob.index(b"\0", cursor, end_struct)
            stack.append(blob[cursor:end].decode())
            cursor = (end + 4) & ~3
            nodes.add("/" + "/".join(part for part in stack if part))
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            name_end = strings.index(b"\0", name_offset)
            name = strings[name_offset:name_end].decode()
            node = "/" + "/".join(part for part in stack if part)
            properties[(node, name)] = blob[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            return nodes, properties
        else:
            raise ValueError(f"Unexpected FDT token {token}: {path}")
    raise ValueError(f"Missing FDT_END: {path}")


def c_string(value: bytes, context: str) -> str:
    if not value.endswith(b"\0") or b"\0" in value[:-1]:
        raise SystemExit(f"Expected one FDT string: {context}")
    return value[:-1].decode()


def resolve(selector: str, properties: dict) -> str:
    if selector.startswith("/"):
        return selector
    key = ("/__symbols__", selector)
    if key not in properties:
        raise SystemExit(f"Missing DT symbol: {selector}")
    return c_string(properties[key], f"symbol {selector}")


def verify_cpu_invariants(nodes: set, properties: dict, name: str) -> None:
    failures = []
    for cpu in ("0", "100", "200", "300", "400", "500", "600", "700"):
        path = f"/cpus/cpu@{cpu}"
        for prop in ("interconnects", "interconnect-names"):
            if (path, prop) in properties:
                failures.append(f"present {path}/{prop}")
        for prop in ("operating-points-v2", "qcom,freq-domain"):
            if (path, prop) not in properties:
                failures.append(f"missing {path}/{prop}")
    expected_counts = {"/opp-table-cpu0": 17, "/opp-table-cpu4": 18, "/opp-table-cpu7": 21}
    for table, expected in expected_counts.items():
        children = [
            node for node in nodes
            if node.startswith(table + "/") and node.count("/") == table.count("/") + 1
        ]
        if len(children) != expected:
            failures.append(f"{table} has {len(children)} OPPs, expected {expected}")
        for child in children:
            if (child, "opp-peak-kBps") in properties:
                failures.append(f"present {child}/opp-peak-kBps")
    if failures:
        raise SystemExit(f"CPU ICC invariant failure in {name}: " + "; ".join(failures))


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: verify-reenable-matrix.py BASELINE_DTB MANIFEST CANDIDATE_DIR"
        )
    baseline_path = Path(sys.argv[1])
    manifest = json.loads(Path(sys.argv[2]).read_text())
    candidate_dir = Path(sys.argv[3])
    if manifest.get("schema") != 2 or len(manifest.get("candidates", [])) != 22:
        raise SystemExit("Unexpected matrix manifest")
    baseline_nodes, baseline = parse_fdt(baseline_path)
    verify_cpu_invariants(baseline_nodes, baseline, "baseline")

    for deferred in manifest.get("deferred", []):
        path = resolve(deferred["node"], baseline)
        if baseline.get((path, "status")) != b"disabled\0":
            raise SystemExit(f"Deferred target is not disabled in baseline: {path}")

    verified = []
    candidate_properties = {}
    for candidate in manifest["candidates"]:
        ident = candidate["id"]
        name = manifest["candidate_prefix"] + ident
        path = candidate_dir / f"{name}.dtb"
        if not path.is_file():
            raise SystemExit(f"Missing candidate DTB: {path}")
        new_nodes, new = parse_fdt(path)
        if baseline_nodes != new_nodes:
            raise SystemExit(f"Node set changed in {ident}")
        expected_paths = {resolve(selector, baseline) for selector in candidate["nodes"]}
        if len(expected_paths) != len(candidate["nodes"]):
            raise SystemExit(f"Two selectors resolve to one node in {ident}")
        expected = {(node, "status") for node in expected_paths}
        for node in expected_paths:
            if baseline.get((node, "status")) != b"disabled\0":
                raise SystemExit(f"Candidate target is not disabled in baseline: {ident}: {node}")
            if new.get((node, "status")) != b"okay\0":
                raise SystemExit(f"Candidate target is not okay: {ident}: {node}")
        icc_tags = candidate.get("uart16_icc_tags")
        if icc_tags is not None:
            if ident not in ACTIVE_ONLY_CANDIDATES or icc_tags != "active-only":
                raise SystemExit(f"Unexpected UART16 ICC override: {ident}: {icc_tags!r}")
            uart = resolve("uart16", baseline)
            key = (uart, "interconnects")
            old_icc = baseline.get(key)
            new_icc = new.get(key)
            if old_icc is None or len(old_icc) != 48:
                raise SystemExit(f"Unexpected baseline UART16 ICC encoding: {old_icc!r}")
            cells = list(struct.unpack(">12I", old_icc))
            tag_cells = (2, 5, 8, 11)
            if any(cells[index] != 0 for index in tag_cells):
                raise SystemExit(f"Baseline UART16 ICC tags are not all zero: {cells}")
            for index in tag_cells:
                cells[index] = 3
            expected_icc = struct.pack(">12I", *cells)
            if new_icc != expected_icc:
                raise SystemExit(
                    f"UART16 ICC tags are not exactly active-only: "
                    f"expected={expected_icc.hex()} actual={None if new_icc is None else new_icc.hex()}"
                )
            expected.add(key)
        uart6_icc_tags = candidate.get("uart6_icc_tags")
        if uart6_icc_tags is not None:
            if (
                ident not in UART6_ACTIVE_ONLY_CANDIDATES
                or uart6_icc_tags != "active-only"
            ):
                raise SystemExit(f"Unexpected UART6 ICC override: {ident}: {uart6_icc_tags!r}")
            uart6 = resolve("uart6", baseline)
            uart6_key = (uart6, "interconnects")
            old_uart6_icc = baseline.get(uart6_key)
            new_uart6_icc = new.get(uart6_key)
            if old_uart6_icc is None or len(old_uart6_icc) != 48:
                raise SystemExit(f"Unexpected baseline UART6 ICC encoding: {old_uart6_icc!r}")
            uart6_cells = list(struct.unpack(">12I", old_uart6_icc))
            tag_cells = (2, 5, 8, 11)
            if any(uart6_cells[index] != 0 for index in tag_cells):
                raise SystemExit(f"Baseline UART6 ICC tags are not all zero: {uart6_cells}")
            for index in tag_cells:
                uart6_cells[index] = 3
            expected_uart6_icc = struct.pack(">12I", *uart6_cells)
            if new_uart6_icc != expected_uart6_icc:
                raise SystemExit(
                    f"UART6 ICC tags are not exactly active-only: "
                    f"expected={expected_uart6_icc.hex()} "
                    f"actual={None if new_uart6_icc is None else new_uart6_icc.hex()}"
                )
            expected.add(uart6_key)
        added = set(new) - set(baseline)
        removed = set(baseline) - set(new)
        changed = {
            key for key in baseline.keys() & new.keys()
            if baseline[key] != new[key]
        }
        if added or removed or changed != expected:
            raise SystemExit(
                f"Unexpected semantic delta in {ident}: added={sorted(added)}, "
                f"removed={sorted(removed)}, changed={sorted(changed)}, "
                f"expected={sorted(expected)}"
            )
        verify_cpu_invariants(new_nodes, new, ident)
        candidate_properties[ident] = new
        verified.append((
            ident,
            len(expected_paths),
            icc_tags is not None,
            uart6_icc_tags is not None,
        ))

    phase2 = candidate_properties["display-gpu-gamepad-active-only"]
    phase3 = candidate_properties["display-gpu-gamepad-active-only-ufs"]
    phase3_delta = {
        key for key in phase2.keys() | phase3.keys()
        if phase2.get(key) != phase3.get(key)
    }
    expected_phase3_delta = {
        (resolve(selector, baseline), "status")
        for selector in ("ufs_mem_hc", "ufs_mem_phy", "vreg_s4a_1p8")
    }
    if phase3_delta != expected_phase3_delta:
        raise SystemExit(
            f"Phase 3 is not an exact three-status delta from Phase 2B: "
            f"actual={sorted(phase3_delta)} expected={sorted(expected_phase3_delta)}"
        )

    phase4 = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless"]
    phase4_delta = {
        key for key in phase3.keys() | phase4.keys()
        if phase3.get(key) != phase4.get(key)
    }
    expected_phase4_delta = {
        (resolve(selector, baseline), "status")
        for selector in ("pcie0", "pcie0_phy", "uart6", "/qca6390-pmu", "qupv3_id_0")
    }
    if phase4_delta != expected_phase4_delta:
        raise SystemExit(
            f"Phase 4 is not an exact five-status delta from Phase 3: "
            f"actual={sorted(phase4_delta)} expected={sorted(expected_phase4_delta)}"
        )

    phase5 = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless-usb-typec"]
    phase5_delta = {
        key for key in phase4.keys() | phase5.keys()
        if phase4.get(key) != phase5.get(key)
    }
    expected_phase5_delta = {
        (resolve(selector, baseline), "status")
        for selector in (
            "usb_1", "usb_1_dwc3", "usb_1_hsphy", "usb_1_qmpphy",
            "pm8150b_typec", "pm8150b_vbus", "i2c15",
        )
    }
    if phase5_delta != expected_phase5_delta:
        raise SystemExit(
            f"Phase 5 is not an exact seven-status delta from Phase 4: "
            f"actual={sorted(phase5_delta)} expected={sorted(expected_phase5_delta)}"
        )

    phase6a = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp"]
    phase6a_delta = {
        key for key in phase5.keys() | phase6a.keys()
        if phase5.get(key) != phase6a.get(key)
    }
    expected_phase6a_delta = {(resolve("mdss_dp", baseline), "status")}
    if phase6a_delta != expected_phase6a_delta:
        raise SystemExit(
            f"Phase 6A is not an exact DisplayPort-status delta from Phase 5: "
            f"actual={sorted(phase6a_delta)} expected={sorted(expected_phase6a_delta)}"
        )

    phase6b = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-adsp-lpass"]
    phase6b_delta = {
        key for key in phase6a.keys() | phase6b.keys()
        if phase6a.get(key) != phase6b.get(key)
    }
    expected_phase6b_delta = {
        (resolve(selector, baseline), "status")
        for selector in ("adsp", "lpass_tlmm")
    }
    if phase6b_delta != expected_phase6b_delta:
        raise SystemExit(
            f"Phase 6B is not an exact ADSP-and-LPASS-status delta from Phase 6A: "
            f"actual={sorted(phase6b_delta)} expected={sorted(expected_phase6b_delta)}"
        )

    phase6c = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless-usb-typec-dp-lpass"]
    phase6c_delta = {
        key for key in phase6a.keys() | phase6c.keys()
        if phase6a.get(key) != phase6c.get(key)
    }
    expected_phase6c_delta = {(resolve("lpass_tlmm", baseline), "status")}
    if phase6c_delta != expected_phase6c_delta:
        raise SystemExit(
            f"Phase 6C is not an exact LPASS-status delta from Phase 6A: "
            f"actual={sorted(phase6c_delta)} expected={sorted(expected_phase6c_delta)}"
        )
    phase6b_from_6c_delta = {
        key for key in phase6c.keys() | phase6b.keys()
        if phase6c.get(key) != phase6b.get(key)
    }
    expected_phase6b_from_6c_delta = {(resolve("adsp", baseline), "status")}
    if phase6b_from_6c_delta != expected_phase6b_from_6c_delta:
        raise SystemExit(
            f"Phase 6B is not an exact ADSP-status delta from Phase 6C: "
            f"actual={sorted(phase6b_from_6c_delta)} expected={sorted(expected_phase6b_from_6c_delta)}"
        )

    phase6 = candidate_properties["display-gpu-gamepad-active-only-ufs-wireless-usb-typec-audio"]
    phase6_delta = {
        key for key in phase6a.keys() | phase6.keys()
        if phase6a.get(key) != phase6.get(key)
    }
    expected_phase6_delta = {
        (resolve(selector, baseline), "status")
        for selector in (
            "adsp", "lpass_tlmm", "sound", "wcd938x", "rxmacro", "txmacro",
            "vamacro", "wsamacro", "swr0", "swr1", "swr2", "vdc_5v",
        )
    }
    expected_phase6_delta.add((resolve("uart6", baseline), "interconnects"))
    if phase6_delta != expected_phase6_delta:
        raise SystemExit(
            f"Phase 6 is not an exact twelve-status plus UART6-tag delta from Phase 6A: "
            f"actual={sorted(phase6_delta)} expected={sorted(expected_phase6_delta)}"
        )

    print(f"Verified baseline node set: {len(baseline_nodes)} nodes")
    print("Verified fixed CPU invariant: 8 ICC paths absent; 56 OPP bandwidth values absent")
    for ident, count, has_icc_override, has_uart6_icc_override in verified:
        suffix = "; UART16 ICC tags exactly active-only" if has_icc_override else ""
        if has_uart6_icc_override:
            suffix += "; UART6 ICC tags exactly active-only"
        print(f"Verified {ident}: exactly {count} disabled-to-okay status changes{suffix}")
    print("Verified Phase 3 relative delta: exactly UFS controller, PHY and shared 1.8 V rail disabled-to-okay")
    print("Verified Phase 4 relative delta: exactly PCIe controller, PCIe PHY, Bluetooth UART, QCA6390 PMU and QUP0 disabled-to-okay")
    print("Verified Phase 5 relative delta: exactly USB controller, DWC3 child, HS PHY, SuperSpeed PHY, PMIC Type-C, PMIC VBUS and I2C15 disabled-to-okay")
    print("Verified Phase 6A relative delta: exactly the required DisplayPort codec provider disabled-to-okay")
    print("Verified Phase 6B relative delta: exactly ADSP and LPASS pinctrl disabled-to-okay")
    print("Verified Phase 6C relative delta: exactly LPASS pinctrl disabled-to-okay; Phase 6B then adds exactly ADSP")
    print("Verified Phase 6 relative delta from Phase 6A: exactly ADSP, LPASS pinctrl, sound card, external codec, four codec macros, three SoundWire controllers and 5 V rail disabled-to-okay; exactly four UART6 ICC tag cells active-only")
    print("Verified 11 independent subsystem candidates plus 11 cumulative integration candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
