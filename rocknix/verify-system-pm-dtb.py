#!/usr/bin/env python3
"""Verify Phase 6M is exact Phase 6H DT plus one system-PM node."""

import struct
import sys
from pathlib import Path


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


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def one_string(value: str) -> bytes:
    return value.encode() + b"\0"


def symbol(properties: dict, name: str) -> str:
    value = properties[("/__symbols__", name)]
    if not value.endswith(b"\0"):
        raise SystemExit(f"Malformed symbol: {name}")
    return value[:-1].decode()


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-system-pm-dtb.py PHASE6H_DTB PHASE6M_DTB")

    baseline_nodes, baseline = parse_fdt(Path(sys.argv[1]))
    candidate_nodes, candidate = parse_fdt(Path(sys.argv[2]))
    node = "/consoleos-system-pm-suspend"

    if candidate_nodes - baseline_nodes != {node} or baseline_nodes - candidate_nodes:
        raise SystemExit(
            f"Unexpected node delta: added={sorted(candidate_nodes - baseline_nodes)}, "
            f"removed={sorted(baseline_nodes - candidate_nodes)}"
        )

    metadata = {"phandle", "linux,phandle"}
    baseline_existing = {
        key: value for key, value in baseline.items()
        if key[0] != "/__symbols__" and key[1] not in metadata
    }
    candidate_existing = {
        key: value for key, value in candidate.items()
        if key[0] != "/__symbols__" and key[0] != node and key[1] not in metadata
    }
    if baseline_existing != candidate_existing:
        changed = sorted(
            key for key in baseline_existing.keys() | candidate_existing.keys()
            if baseline_existing.get(key) != candidate_existing.get(key)
        )
        raise SystemExit(f"Existing DT properties changed: {changed}")

    apps_rsc_path = symbol(candidate, "apps_rsc")
    apps_rsc_phandle = candidate.get((apps_rsc_path, "phandle"))
    if not apps_rsc_phandle:
        raise SystemExit("Apps RSC phandle missing")

    required = {
        (node, "compatible"): one_string("qcom,consoleos-system-pm-suspend"),
        (node, "qcom,psci-suspend-state"): u32(0x4100C244),
        (node, "qcom,rpmh-controller"): apps_rsc_phandle,
    }
    actual = {
        key: value for key, value in candidate.items()
        if key[0] == node and key[1] not in metadata
    }
    if actual != required:
        raise SystemExit(
            "System-PM node mismatch: "
            f"expected={sorted(required)}, actual={sorted(actual)}"
        )

    slpi_path = symbol(candidate, "slpi")
    adsp_path = symbol(candidate, "adsp")
    lpass_path = symbol(candidate, "lpass_tlmm")
    if candidate.get((slpi_path, "status")) != one_string("disabled"):
        raise SystemExit("SLPI is not disabled")
    if candidate.get((adsp_path, "status")) != one_string("okay"):
        raise SystemExit("ADSP is not enabled")
    if candidate.get((lpass_path, "status")) != one_string("disabled"):
        raise SystemExit("LPASS pinctrl is not disabled")

    print("Verified exact Phase 6H DT plus one system-PM suspend node")
    print("Verified Apps RSC phandle, exact PSCI 0x4100c244, and no other semantic delta")
    print("Verified SLPI disabled, ADSP enabled and LPASS pinctrl disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
