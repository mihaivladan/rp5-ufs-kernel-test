#!/usr/bin/env python3
"""Generate auditable DT sources for the RP5 subsystem re-enable matrix."""

import json
import re
import sys
from pathlib import Path


ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema") != 1:
        raise SystemExit("Unsupported re-enable matrix schema")
    if data.get("baseline") != "sm8250-retroidpocket-rp5-stock-pruned":
        raise SystemExit("Unexpected matrix baseline")
    if data.get("candidate_prefix") != "sm8250-retroidpocket-rp5-reenable-":
        raise SystemExit("Unexpected matrix candidate prefix")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 14:
        raise SystemExit("Expected eleven independent candidates and three cumulative integration candidates")
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
        ids.append(ident)
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate candidate id")
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
            '#include "sm8250-retroidpocket-rp5-stock-pruned.dts"',
            "",
        ]
        lines.extend(f'{target(node)} {{ status = "okay"; }};' for node in candidate["nodes"])
        path = output / f"{basename}.dts"
        path.write_text("\n".join(lines) + "\n")
        generated.append(path.name)
    print(f"Generated {len(generated)} candidate Device Trees")
    for name in generated:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
