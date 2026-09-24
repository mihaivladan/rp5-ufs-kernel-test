#!/usr/bin/env python3
"""Expose a failed build's tail as a public GitHub Actions annotation."""

from pathlib import Path
import sys


def escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


path = Path(sys.argv[1])
line_count = int(sys.argv[2]) if len(sys.argv) > 2 else 120
if not path.is_file():
    tail = f"diagnostic log was not created: {path}"
else:
    lines = path.read_text(errors="replace").splitlines()
    tail = "\n".join(lines[-line_count:])

# GitHub truncates annotation messages to 4096 characters from the end we
# need most.  Bound the unescaped input so percent-encoded newlines still fit.
tail = tail[-1800:]

print(f"::error title=Build failure tail::{escape(tail)}")
