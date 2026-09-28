#!/usr/bin/env python3
"""Verify the RP5 RPMh control/treatment DTBs differ only by three mode policies."""

from pathlib import Path
import struct
import sys


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


def symbol(properties: dict, name: str) -> str:
    value = properties[("/__symbols__", name)]
    if not value.endswith(b"\0"):
        raise SystemExit(f"Malformed symbol: {name}")
    return value[:-1].decode()


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: verify-rpmh-sleep-policy-dtb.py BASE CONTROL TREATMENT")

    base_path, control_path, treatment_path = map(Path, sys.argv[1:])
    if base_path.read_bytes() != control_path.read_bytes():
        raise SystemExit("Control DTB is not byte-identical to the accepted Phase 4AU full-stack DTB")

    old_nodes, old = parse_fdt(control_path)
    new_nodes, new = parse_fdt(treatment_path)
    target_modes = {
        "vreg_l5a_0p88": 1,  # RPMH_REGULATOR_MODE_LPM
        "vreg_l6a_1p2": 1,  # RPMH_REGULATOR_MODE_LPM
        "vreg_s8c_1p3": 0,  # RPMH_REGULATOR_MODE_RET
    }
    expected_nodes = {
        symbol(new, label) + "/regulator-state-mem"
        for label in target_modes
    }
    if new_nodes - old_nodes != expected_nodes or old_nodes - new_nodes:
        raise SystemExit(
            f"Unexpected node delta: added={sorted(new_nodes - old_nodes)}, "
            f"removed={sorted(old_nodes - new_nodes)}"
        )

    expected_added = {}
    for label, mode in target_modes.items():
        state = symbol(new, label) + "/regulator-state-mem"
        expected_added[(state, "regulator-on-in-suspend")] = b""
        expected_added[(state, "regulator-mode")] = u32(mode)

    metadata = {"phandle", "linux,phandle"}
    added = {
        key: value for key, value in new.items()
        if key not in old and key[0] != "/__symbols__" and key[1] not in metadata
    }
    removed = {
        key for key in old if key not in new
        and key[0] != "/__symbols__" and key[1] not in metadata
    }
    changed = {
        key for key in old.keys() & new.keys()
        if old[key] != new[key]
        and key[0] != "/__symbols__" and key[1] not in metadata
    }
    if added != expected_added or removed or changed:
        raise SystemExit(
            f"Unexpected property delta: added={sorted(added)}, "
            f"removed={sorted(removed)}, changed={sorted(changed)}"
        )

    forbidden = {
        "regulator-off-in-suspend",
        "regulator-suspend-microvolt",
        "regulator-min-microvolt",
        "regulator-max-microvolt",
    }
    for node in expected_nodes:
        present = {name for path, name in new if path == node}
        if present & forbidden:
            raise SystemExit(f"Forbidden treatment property at {node}: {sorted(present & forbidden)}")

    print("Verified control is byte-identical to the accepted Phase 4AU full-stack DTB")
    print("Verified treatment adds exactly three enabled state_mem nodes")
    print("Verified L5A/L6A=LPM, S8C=RET and no disable or voltage policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
