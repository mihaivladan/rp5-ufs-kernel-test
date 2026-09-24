#!/usr/bin/env python3
"""Verify the RP5 kernel-ordering DT changes only ADSP firmware and QCA dependency."""

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


def one_string(value: str) -> bytes:
    return value.encode() + b"\0"


def symbol(properties: dict, name: str) -> str:
    value = properties[("/__symbols__", name)]
    if not value.endswith(b"\0"):
        raise SystemExit(f"Malformed symbol: {name}")
    return value[:-1].decode()


def phandle_paths(properties: dict) -> dict[int, str]:
    """Map compiled phandle numbers back to stable node paths."""
    result = {}
    for (path, name), value in properties.items():
        if name not in {"phandle", "linux,phandle"} or len(value) != 4:
            continue
        number = struct.unpack(">I", value)[0]
        previous = result.setdefault(number, path)
        if previous != path:
            raise SystemExit(f"Duplicate phandle {number}: {previous}, {path}")
    return result


def equal_after_phandle_renumbering(
    baseline_value: bytes,
    candidate_value: bytes,
    baseline_phandles: dict[int, str],
    candidate_phandles: dict[int, str],
) -> bool:
    """Accept changed cells only when both still name the same target node."""
    if len(baseline_value) != len(candidate_value) or len(baseline_value) % 4:
        return False
    for offset in range(0, len(baseline_value), 4):
        baseline_cell = struct.unpack_from(">I", baseline_value, offset)[0]
        candidate_cell = struct.unpack_from(">I", candidate_value, offset)[0]
        if baseline_cell == candidate_cell:
            continue
        baseline_path = baseline_phandles.get(baseline_cell)
        candidate_path = candidate_phandles.get(candidate_cell)
        if baseline_path is None or baseline_path != candidate_path:
            return False
    return True


def main() -> int:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "usage: verify-adsp-before-bluetooth-dtb.py "
            "BASELINE_DTB CANDIDATE_DTB [EXPECTED_SOUND_STATUS]"
        )
    expected_sound_status = sys.argv[3] if len(sys.argv) == 4 else "okay"
    if expected_sound_status not in {"okay", "disabled"}:
        raise SystemExit(f"Unexpected sound status: {expected_sound_status}")

    baseline_nodes, baseline = parse_fdt(Path(sys.argv[1]))
    candidate_nodes, candidate = parse_fdt(Path(sys.argv[2]))
    if candidate_nodes != baseline_nodes:
        raise SystemExit(
            f"Unexpected node delta: added={sorted(candidate_nodes - baseline_nodes)}, "
            f"removed={sorted(baseline_nodes - candidate_nodes)}"
        )

    adsp_path = symbol(candidate, "adsp")
    if symbol(baseline, "adsp") != adsp_path:
        raise SystemExit("ADSP symbol changed between baseline and candidate")
    bluetooth_path = "/soc@0/geniqup@9c0000/serial@998000/bluetooth"
    allowed = {
        (adsp_path, "firmware-name"),
        (bluetooth_path, "qcom,rproc"),
    }
    metadata = {"phandle", "linux,phandle"}
    baseline_existing = {
        key: value for key, value in baseline.items()
        if key[0] != "/__symbols__" and key[1] not in metadata and key not in allowed
    }
    candidate_existing = {
        key: value for key, value in candidate.items()
        if key[0] != "/__symbols__" and key[1] not in metadata and key not in allowed
    }
    baseline_phandles = phandle_paths(baseline)
    candidate_phandles = phandle_paths(candidate)
    changed = []
    for key in sorted(baseline_existing.keys() | candidate_existing.keys()):
        baseline_value = baseline_existing.get(key)
        candidate_value = candidate_existing.get(key)
        if baseline_value == candidate_value:
            continue
        if baseline_value is not None and candidate_value is not None and \
                equal_after_phandle_renumbering(
                    baseline_value,
                    candidate_value,
                    baseline_phandles,
                    candidate_phandles,
                ):
            continue
        changed.append(key)
    if changed:
        raise SystemExit(f"Unexpected existing-property delta: {changed}")

    failures = []
    if baseline.get((adsp_path, "firmware-name")) != one_string("qcom/sm8250/adsp.mbn"):
        failures.append("baseline ADSP firmware")
    if candidate.get((adsp_path, "firmware-name")) != one_string("adsp.mdt"):
        failures.append("candidate ADSP firmware")
    adsp_phandle = candidate.get((adsp_path, "phandle"))
    if not adsp_phandle or candidate.get((bluetooth_path, "qcom,rproc")) != adsp_phandle:
        failures.append("Bluetooth qcom,rproc ADSP phandle")
    if (bluetooth_path, "qcom,rproc") in baseline:
        failures.append("baseline unexpectedly has qcom,rproc")
    for path in (adsp_path, symbol(candidate, "sound")):
        expected = "okay" if path == adsp_path else expected_sound_status
        if candidate.get((path, "status")) != one_string(expected):
            failures.append(f"{path}/status")
    # A missing status property means enabled in Device Tree.  The RP5 QCA
    # Bluetooth child uses that implicit form in the exact release tree.
    bluetooth_status = candidate.get((bluetooth_path, "status"), one_string("okay"))
    if bluetooth_status != one_string("okay"):
        failures.append(f"{bluetooth_path}/status")
    baseline_sound = symbol(baseline, "sound")
    if baseline.get((baseline_sound, "status")) != one_string(expected_sound_status):
        failures.append(f"{baseline_sound}/status")
    if failures:
        raise SystemExit("ADSP-before-Bluetooth DT verification failed: " + ", ".join(failures))

    print("Verified baseline semantics with no node additions or removals")
    print("Verified only ADSP firmware-name and Bluetooth qcom,rproc semantics changed")
    print("Verified QCA Bluetooth dependency resolves to the enabled ADSP remoteproc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
