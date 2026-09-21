#!/usr/bin/env python3
"""Prove stock-pruned differs from minsleep4 only by two disabled nodes."""

from pathlib import Path
import struct
import sys


def parse_fdt(path: Path):
    blob = path.read_bytes()
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


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-stock-pruned-dtb.py MINsleep4_DTB CANDIDATE_DTB")
    old_nodes, old = parse_fdt(Path(sys.argv[1]))
    new_nodes, new = parse_fdt(Path(sys.argv[2]))
    if old_nodes != new_nodes:
        raise SystemExit("Node set changed")
    expected = {
        ("/soc@0/pinctrl@33c0000", "status"),
        ("/soc@0/codec@3370000", "status"),
    }
    added = set(new) - set(old)
    removed = set(old) - set(new)
    changed = {key for key in old.keys() & new.keys() if old[key] != new[key]}
    if removed or changed or added != expected:
        raise SystemExit(
            f"Unexpected semantic delta: added={sorted(added)}, removed={sorted(removed)}, "
            f"changed={sorted(changed)}"
        )
    for key in expected:
        if new[key] != b"disabled\0":
            raise SystemExit(f"Wrong status value: {key}")
    print(f"Verified identical node set: {len(old_nodes)} nodes")
    print("Verified exact delta from minsleep4: two added status=disabled properties")
    print("  /soc@0/pinctrl@33c0000")
    print("  /soc@0/codec@3370000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
