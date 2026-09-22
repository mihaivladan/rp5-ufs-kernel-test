#!/usr/bin/env python3
"""Verify the RP5 SMP2P sleep-state DT is an exact Phase 6D delta."""

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
        raise SystemExit("usage: verify-sleepstate-dtb.py PHASE6D_DTB HANDSHAKE_DTB")

    baseline_nodes, baseline = parse_fdt(Path(sys.argv[1]))
    candidate_nodes, candidate = parse_fdt(Path(sys.argv[2]))
    new_nodes = {
        "/smp2p-slpi/sleepstate-out",
        "/smp2p-slpi/sleepstate-in",
        "/smp2p-sleepstate",
    }
    if candidate_nodes - baseline_nodes != new_nodes or baseline_nodes - candidate_nodes:
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
        if key[0] != "/__symbols__" and key[0] not in new_nodes and key[1] not in metadata
    }
    if baseline_existing != candidate_existing:
        changed = sorted(
            key for key in baseline_existing.keys() | candidate_existing.keys()
            if baseline_existing.get(key) != candidate_existing.get(key)
        )
        raise SystemExit(f"Existing DT properties changed: {changed}")

    outbound = "/smp2p-slpi/sleepstate-out"
    inbound = "/smp2p-slpi/sleepstate-in"
    consumer = "/smp2p-sleepstate"
    required = {
        (outbound, "qcom,entry-name"): one_string("sleepstate"),
        (outbound, "#qcom,smem-state-cells"): u32(1),
        (inbound, "qcom,entry-name"): one_string("sleepstate_see"),
        (inbound, "interrupt-controller"): b"",
        (inbound, "#interrupt-cells"): u32(2),
        (consumer, "compatible"): one_string("qcom,smp2p-sleepstate"),
        (consumer, "interrupts"): u32(0) + u32(0),
        (consumer, "interrupt-names"): one_string("smp2p-sleepstate-in"),
    }
    failures = [
        f"{node}/{name}" for (node, name), value in required.items()
        if candidate.get((node, name)) != value
    ]

    outbound_phandle = candidate.get((outbound, "phandle"))
    inbound_phandle = candidate.get((inbound, "phandle"))
    if not outbound_phandle or candidate.get((consumer, "qcom,smem-states")) != outbound_phandle + u32(0):
        failures.append(f"{consumer}/qcom,smem-states")
    if not inbound_phandle or candidate.get((consumer, "interrupt-parent")) != inbound_phandle:
        failures.append(f"{consumer}/interrupt-parent")

    if candidate.get(("/smp2p-slpi", "qcom,remote-pid")) != u32(3):
        failures.append("/smp2p-slpi/qcom,remote-pid")
    if candidate.get(("/smp2p-adsp", "qcom,remote-pid")) != u32(2):
        failures.append("/smp2p-adsp/qcom,remote-pid")
    if candidate.get((symbol(candidate, "adsp"), "status")) != one_string("okay"):
        failures.append("adsp/status")
    if candidate.get((symbol(candidate, "lpass_tlmm"), "status")) != one_string("disabled"):
        failures.append("lpass_tlmm/status")

    for cpu in ("0", "100", "200", "300", "400", "500", "600", "700"):
        path = f"/cpus/cpu@{cpu}"
        if (path, "interconnects") in candidate or (path, "interconnect-names") in candidate:
            failures.append(f"{path}/CPU-ICC")

    if failures:
        raise SystemExit("Sleep-state DT verification failed: " + ", ".join(failures))

    print("Verified exact Phase 6D DT plus three sleep-state nodes")
    print("Verified sleepstate/sleepstate_see route to SLPI remote PID 3, not ADSP PID 2")
    print("Verified ADSP enabled, LPASS pinctrl disabled and all eight CPU ICC paths absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
