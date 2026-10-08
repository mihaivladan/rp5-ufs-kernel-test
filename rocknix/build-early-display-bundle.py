#!/usr/bin/env python3
"""Build the deterministic external ConsoleOS early-display initramfs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile


ARCHIVE_ROOT = Path("consoleos/early-display-external")
RUNTIME_ROOT = Path("/consoleos/early-display-external")
CPIO_MAGIC = b"070701"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def align4(value: int) -> int:
    return (value + 3) & ~3


def newc_entry(name: str, mode: int, payload: bytes, inode: int) -> bytes:
    encoded_name = name.encode("ascii") + b"\0"
    fields = (
        inode,
        mode,
        0,
        0,
        1,
        0,
        len(payload),
        0,
        0,
        0,
        0,
        len(encoded_name),
        0,
    )
    header = CPIO_MAGIC + b"".join(f"{field:08x}".encode("ascii") for field in fields)
    result = header + encoded_name
    result += b"\0" * (align4(len(result)) - len(result))
    result += payload
    result += b"\0" * (align4(len(result)) - len(result))
    return result


def pack_tree(root: Path) -> tuple[bytes, list[dict[str, object]]]:
    paths = sorted(
        (path for path in root.rglob("*") if path.is_dir() or path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    archive = bytearray()
    report: list[dict[str, object]] = []
    inode = 1
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if not relative.isascii():
            raise SystemExit(f"non-ASCII archive path: {relative}")
        if path.is_dir():
            mode = stat.S_IFDIR | 0o755
            payload = b""
        else:
            mode = stat.S_IFREG | (0o755 if path.name == "consoleos-early-splash" else 0o644)
            payload = path.read_bytes()
            report.append(
                {"path": relative, "bytes": len(payload), "sha256": sha256(payload)}
            )
        archive.extend(newc_entry(relative, mode, payload, inode))
        inode += 1
    archive.extend(newc_entry("TRAILER!!!", 0, b"", inode))
    archive.extend(b"\0" * ((512 - len(archive) % 512) % 512))
    return bytes(archive), report


def build_once(source: Path, compiler: list[str], parent: Path, sequence: int) -> tuple[bytes, list[dict[str, object]]]:
    work = parent / f"build-{sequence}"
    stage = work / "stage"
    bundle = stage / ARCHIVE_ROOT
    binary = bundle / "usr/bin/consoleos-early-splash"
    assets = bundle / "usr/share/consoleos/boot-display"
    header = work / "consoleos-early-display-profiles.h"
    binary.parent.mkdir(parents=True)
    assets.parent.mkdir(parents=True)
    shutil.copytree(source / "boot-display/profiles", assets / "profiles")
    subprocess.run(
        [
            "python3",
            str(source / "generate-early-display-profiles.py"),
            "--profiles",
            str(source / "boot-display/profiles"),
            "--asset-root",
            str(RUNTIME_ROOT / "usr/share/consoleos/boot-display/profiles"),
            "--output",
            str(header),
        ],
        check=True,
    )
    subprocess.run(
        compiler
        + [
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DCONSOLEOS_EXTERNAL_BUNDLE=1",
            f"-ffile-prefix-map={work}=.",
            "-I",
            str(work),
            str(source / "consoleos-early-splash.c"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    entries = sorted(
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    )
    manifest = "".join(
        f"{sha256((bundle / entry).read_bytes())}  {entry}\n" for entry in entries
    ).encode("ascii")
    (bundle / "SHA256SUMS").write_bytes(manifest)
    return pack_tree(stage)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--cc", default=os.environ.get("HOST_CC", "gcc-14"))
    args = parser.parse_args()
    source = args.source.resolve()
    compiler = shlex.split(args.cc)
    if not compiler:
        raise SystemExit("empty compiler command")
    with tempfile.TemporaryDirectory(prefix="consoleos-early-display.") as temporary:
        parent = Path(temporary)
        first, entries = build_once(source, compiler, parent, 1)
        second, _ = build_once(source, compiler, parent, 2)
    if first != second:
        raise SystemExit("external early-display CPIO is not reproducible")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_name(args.output.name + ".tmp")
    temporary_output.write_bytes(first)
    os.replace(temporary_output, args.output)
    report = {
        "schema": "consoleos-early-display-bundle-v1",
        "archive_root": ARCHIVE_ROOT.as_posix(),
        "runtime_root": str(RUNTIME_ROOT),
        "bytes": len(first),
        "sha256": sha256(first),
        "reproducible_two_builds": True,
        "entries": entries,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
