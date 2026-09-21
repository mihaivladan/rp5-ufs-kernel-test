#!/usr/bin/env python3
"""Prove the full RP5 candidate differs from stock only by CPU ICC data."""

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
    if off_strings + strings_len > size or off_struct + struct_len > size:
        raise ValueError(f"FDT block out of bounds: {path}")
    strings = blob[off_strings:off_strings + strings_len]
    cursor = off_struct
    end_struct = off_struct + struct_len
    stack = []
    nodes = set()
    properties = {}
    while cursor < end_struct:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:  # FDT_BEGIN_NODE
            end = blob.index(b"\0", cursor, end_struct)
            stack.append(blob[cursor:end].decode())
            cursor = (end + 4) & ~3
            nodes.add("/" + "/".join(part for part in stack if part))
        elif token == 2:  # FDT_END_NODE
            if not stack:
                raise ValueError(f"Unbalanced node end: {path}")
            stack.pop()
        elif token == 3:  # FDT_PROP
            length, name_offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            if name_offset >= strings_len or cursor + length > end_struct:
                raise ValueError(f"Invalid property bounds: {path}")
            name_end = strings.index(b"\0", name_offset)
            name = strings[name_offset:name_end].decode()
            node = "/" + "/".join(part for part in stack if part)
            key = (node, name)
            if key in properties:
                raise ValueError(f"Duplicate property {node}/{name}: {path}")
            properties[key] = blob[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            if stack:
                raise ValueError(f"Unclosed nodes: {path}")
            return nodes, properties
        else:
            raise ValueError(f"Unexpected FDT token {token}: {path}")
    raise ValueError(f"Missing FDT_END: {path}")


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-cpu-icc-off-dtb.py STOCK_DTB CANDIDATE_DTB")
    stock_path, candidate_path = map(Path, sys.argv[1:])
    stock_nodes, stock = parse_fdt(stock_path)
    candidate_nodes, candidate = parse_fdt(candidate_path)
    if stock_nodes != candidate_nodes:
        missing = sorted(stock_nodes - candidate_nodes)
        added = sorted(candidate_nodes - stock_nodes)
        raise SystemExit(f"Node set changed; missing={missing}, added={added}")

    expected_removed = set()
    cpu_paths = ("/cpus/cpu@0",) + tuple(f"/cpus/cpu@{index}00" for index in range(1, 8))
    for cpu in cpu_paths:
        expected_removed.add((cpu, "interconnects"))
        expected_removed.add((cpu, "interconnect-names"))
        for retained in ("operating-points-v2", "qcom,freq-domain"):
            key = (cpu, retained)
            if key not in stock or candidate.get(key) != stock[key]:
                raise SystemExit(f"CPU frequency property missing or changed: {cpu}/{retained}")

    expected_opp_counts = {
        "/opp-table-cpu0": 17,
        "/opp-table-cpu4": 18,
        "/opp-table-cpu7": 21,
    }
    for table, expected_count in expected_opp_counts.items():
        keys = {
            key for key in stock
            if key[0].startswith(table + "/opp-") and key[1] == "opp-peak-kBps"
        }
        if len(keys) != expected_count:
            raise SystemExit(
                f"Expected {expected_count} stock OPP bandwidth properties under {table}, found {len(keys)}"
            )
        expected_removed.update(keys)

    removed = set(stock) - set(candidate)
    added = set(candidate) - set(stock)
    changed = {key for key in stock.keys() & candidate.keys() if stock[key] != candidate[key]}
    if removed != expected_removed:
        raise SystemExit(
            f"Removed property set mismatch; missing={sorted(expected_removed - removed)}, "
            f"unexpected={sorted(removed - expected_removed)}"
        )
    if added:
        raise SystemExit(f"Unexpected added properties: {sorted(added)}")
    if changed:
        raise SystemExit(f"Unexpected changed properties: {sorted(changed)}")

    model = candidate.get(("/", "model"), b"").rstrip(b"\0").decode()
    if model != "Retroid Pocket 5":
        raise SystemExit(f"Wrong model: {model!r}")
    print(f"Verified identical node set: {len(stock_nodes)} nodes")
    print("Verified exactly 72 removed properties and no additions or value changes:")
    print("  16 CPU interconnect/interconnect-name properties across 8 CPUs")
    print("  56 opp-peak-kBps properties across 17 + 18 + 21 CPU OPPs")
    print("Verified operating-points-v2 and qcom,freq-domain unchanged on all 8 CPUs")
    print("Verified full Retroid Pocket 5 hardware tree otherwise byte-for-byte semantic equal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
