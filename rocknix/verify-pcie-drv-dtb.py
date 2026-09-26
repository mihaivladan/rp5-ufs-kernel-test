#!/usr/bin/env python3
"""Verify the PCIe DRV candidate is an exact Phase 4 wireless delta."""

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
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-pcie-drv-dtb.py PHASE4_DTB CANDIDATE_DTB")

    old_nodes, old = parse_fdt(Path(sys.argv[1]))
    new_nodes, new = parse_fdt(Path(sys.argv[2]))
    if old_nodes != new_nodes:
        raise SystemExit("Node set changed")

    pcie = symbol(new, "pcie0")
    adsp = symbol(new, "adsp")
    expected_added = {
        (pcie, "qcom,drv-supported"): b"",
        (pcie, "qcom,drv-dev-id"): u32(0),
        (pcie, "qcom,drv-l1ss-timeout-us"): u32(10000),
    }
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
    if added != expected_added or removed or changed != {(adsp, "status")}:
        raise SystemExit(
            f"Unexpected semantic delta: added={sorted(added)}, "
            f"removed={sorted(removed)}, changed={sorted(changed)}"
        )
    if old[(adsp, "status")] != b"disabled\0" or new[(adsp, "status")] != b"okay\0":
        raise SystemExit("ADSP status is not the exact disabled-to-okay transition")
    if new.get((pcie, "status")) != b"okay\0":
        raise SystemExit("PCIe0 is not enabled")

    for cpu in ("0", "100", "200", "300", "400", "500", "600", "700"):
        path = f"/cpus/cpu@{cpu}"
        if (path, "interconnects") in new or (path, "interconnect-names") in new:
            raise SystemExit(f"CPU ICC unexpectedly present: {path}")

    print("Verified exact Phase 4 wireless tree plus running ADSP and three PCIe DRV properties")
    print("Verified RC0, 10 ms L1SS timeout, enabled PCIe0 and absent CPU ICC paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
