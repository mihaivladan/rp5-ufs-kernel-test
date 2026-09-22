#!/usr/bin/env python3
"""Verify exact semantic deltas for every RP5 subsystem re-enable candidate."""

import json
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
    if manifest.get("schema") != 1 or len(manifest.get("candidates", [])) != 14:
        raise SystemExit("Unexpected matrix manifest")
    baseline_nodes, baseline = parse_fdt(baseline_path)
    verify_cpu_invariants(baseline_nodes, baseline, "baseline")

    for deferred in manifest.get("deferred", []):
        path = resolve(deferred["node"], baseline)
        if baseline.get((path, "status")) != b"disabled\0":
            raise SystemExit(f"Deferred target is not disabled in baseline: {path}")

    verified = []
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
        verified.append((ident, len(expected)))

    print(f"Verified baseline node set: {len(baseline_nodes)} nodes")
    print("Verified fixed CPU invariant: 8 ICC paths absent; 56 OPP bandwidth values absent")
    for ident, count in verified:
        print(f"Verified {ident}: exactly {count} disabled-to-okay status changes")
    print("Verified 11 independent subsystem candidates plus 3 cumulative integration candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
