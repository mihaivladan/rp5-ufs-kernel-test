#!/usr/bin/env python3
"""Generate auditable DT sources for the RP5 subsystem re-enable matrix."""

import json
import re
import sys
from pathlib import Path


ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ACTIVE_ONLY_CANDIDATES = {
    "display-gpu-gamepad-active-only",
    "display-gpu-gamepad-active-only-ufs",
    "display-gpu-gamepad-active-only-ufs-wireless",
    "display-gpu-gamepad-active-only-ufs-wireless-usb-typec",
}


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema") != 2:
        raise SystemExit("Unsupported re-enable matrix schema")
    if data.get("baseline") != "sm8250-retroidpocket-rp5-stock-pruned":
        raise SystemExit("Unexpected matrix baseline")
    if data.get("candidate_prefix") != "sm8250-retroidpocket-rp5-reenable-":
        raise SystemExit("Unexpected matrix candidate prefix")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 18:
        raise SystemExit("Expected eleven independent candidates and seven cumulative integration candidates")
    ids = []
    for candidate in candidates:
        ident = candidate.get("id")
        nodes = candidate.get("nodes")
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise SystemExit(f"Invalid candidate id: {ident!r}")
        if not isinstance(candidate.get("description"), str) or not candidate["description"]:
            raise SystemExit(f"Missing description: {ident}")
        if not isinstance(nodes, list) or not nodes or len(nodes) != len(set(nodes)):
            raise SystemExit(f"Invalid or duplicate node list: {ident}")
        for node in nodes:
            if not isinstance(node, str):
                raise SystemExit(f"Non-string selector in {ident}")
            if node.startswith("/"):
                if not node.startswith("/") or any(c in node for c in "{};\n\r"):
                    raise SystemExit(f"Unsafe path selector: {node!r}")
            elif not LABEL.fullmatch(node):
                raise SystemExit(f"Unsafe label selector: {node!r}")
        icc_tags = candidate.get("uart16_icc_tags")
        if icc_tags not in (None, "active-only"):
            raise SystemExit(f"Invalid UART16 ICC tag mode: {ident}: {icc_tags!r}")
        if icc_tags and (ident not in ACTIVE_ONLY_CANDIDATES or "uart16" not in nodes):
            raise SystemExit(f"UART16 ICC override is outside its exact candidate: {ident}")
        ids.append(ident)
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate candidate id")
    actual_active_only = {
        candidate["id"]
        for candidate in candidates
        if candidate.get("uart16_icc_tags") == "active-only"
    }
    if actual_active_only != ACTIVE_ONLY_CANDIDATES:
        raise SystemExit(
            f"Unexpected UART16 active-only candidate set: {sorted(actual_active_only)}"
        )
    by_id = {candidate["id"]: candidate for candidate in candidates}
    phase2_nodes = set(by_id["display-gpu-gamepad-active-only"]["nodes"])
    phase3_nodes = set(by_id["display-gpu-gamepad-active-only-ufs"]["nodes"])
    if phase3_nodes != phase2_nodes | {"ufs_mem_hc", "ufs_mem_phy", "vreg_s4a_1p8"}:
        raise SystemExit("Phase 3 must add exactly the three-node UFS slice to Phase 2B")
    phase4_nodes = set(by_id["display-gpu-gamepad-active-only-ufs-wireless"]["nodes"])
    phase4_added = {"pcie0", "pcie0_phy", "uart6", "/qca6390-pmu", "qupv3_id_0"}
    if phase4_nodes != phase3_nodes | phase4_added:
        raise SystemExit("Phase 4 must add exactly the five new wireless nodes to Phase 3")
    phase5_nodes = set(by_id["display-gpu-gamepad-active-only-ufs-wireless-usb-typec"]["nodes"])
    phase5_added = {
        "usb_1", "usb_1_dwc3", "usb_1_hsphy", "usb_1_qmpphy",
        "pm8150b_typec", "pm8150b_vbus", "i2c15",
    }
    if phase5_nodes != phase4_nodes | phase5_added:
        raise SystemExit("Phase 5 must add exactly the seven new USB/Type-C nodes to Phase 4")
    deferred = data.get("deferred")
    if not isinstance(deferred, list) or len(deferred) != 3:
        raise SystemExit("Expected three explicitly deferred targets")
    return data


def target(selector: str) -> str:
    return f"&{{{selector}}}" if selector.startswith("/") else f"&{selector}"


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate-reenable-matrix.py MANIFEST OUTPUT_DIR")
    manifest_path = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    data = load_manifest(manifest_path)
    output.mkdir(parents=True, exist_ok=True)
    generated = []
    for candidate in data["candidates"]:
        basename = data["candidate_prefix"] + candidate["id"]
        lines = [
            "// SPDX-License-Identifier: BSD-3-Clause",
            "/* Generated from rocknix/reenable-matrix.json; do not edit by hand.",
            f" * Candidate: {candidate['id']}",
            f" * {candidate['description']}",
            " */",
            "",
        ]
        if candidate.get("uart16_icc_tags") == "active-only":
            lines.extend([
                "#include <dt-bindings/interconnect/qcom,icc.h>",
                "",
            ])
        lines.extend([
            '#include "sm8250-retroidpocket-rp5-stock-pruned.dts"',
            "",
        ])
        special_uart = candidate.get("uart16_icc_tags") == "active-only"
        lines.extend(
            f'{target(node)} {{ status = "okay"; }};'
            for node in candidate["nodes"]
            if not (special_uart and node == "uart16")
        )
        if special_uart:
            lines.extend([
                "",
                "&uart16 {",
                '    status = "okay";',
                "    interconnects =",
                "        <&qup_virt MASTER_QUP_CORE_2 QCOM_ICC_TAG_ACTIVE_ONLY",
                "         &qup_virt SLAVE_QUP_CORE_2 QCOM_ICC_TAG_ACTIVE_ONLY>,",
                "        <&gem_noc MASTER_AMPSS_M0 QCOM_ICC_TAG_ACTIVE_ONLY",
                "         &config_noc SLAVE_QUP_2 QCOM_ICC_TAG_ACTIVE_ONLY>;",
                "};",
            ])
        path = output / f"{basename}.dts"
        path.write_text("\n".join(lines) + "\n")
        generated.append(path.name)
    print(f"Generated {len(generated)} candidate Device Trees")
    for name in generated:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
