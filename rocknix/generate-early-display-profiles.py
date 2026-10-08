#!/usr/bin/env python3
"""Generate the initramfs early-display profile table from device assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import struct


MAX_PROFILES = 32
MAX_COMPATIBLES = 16
REQUIRED = {
    "schema",
    "profile_id",
    "device_tree_compatible_count",
    "native_width",
    "native_height",
    "native_stride",
    "pixel_format",
    "linux_fbdev_width",
    "linux_fbdev_height",
    "linux_fbdev_stride_bytes",
    "linux_fbdev_pixel_format",
    "linux_fbdev_rotation",
    "rotation",
    "canvas_width",
    "canvas_height",
    "logo_x",
    "logo_y",
    "logo_width",
    "logo_height",
    "logo_payload_bytes",
    "logo_payload_sha256",
    "linux_fbdev_logo_payload_bytes",
    "linux_fbdev_logo_payload_sha256",
}


def parse_cfg(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    raw = path.read_bytes()
    if len(raw) > 4096 or b"\0" in raw:
        raise SystemExit(f"{path}: oversized or binary device.cfg")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise SystemExit(f"{path}: device.cfg is not ASCII") from error
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path}: malformed line: {line!r}")
        key, value = line.split("=", 1)
        if key in values:
            raise SystemExit(f"{path}: duplicate key: {key}")
        values[key] = value
    missing = sorted(REQUIRED - values.keys())
    if missing:
        raise SystemExit(f"{path}: missing keys: {', '.join(missing)}")
    if values["schema"] != "redika-boot-device-v1":
        raise SystemExit(f"{path}: unsupported schema")
    return values


def bounded_int(values: dict[str, str], key: str, minimum: int, maximum: int) -> int:
    value = values[key]
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise SystemExit(f"{key}: malformed integer: {value!r}")
    number = int(value)
    if not minimum <= number <= maximum:
        raise SystemExit(f"{key}: {number} outside {minimum}..{maximum}")
    return number


def c_string(value: str) -> str:
    if not value.isascii() or any(ord(char) < 0x20 for char in value):
        raise SystemExit(f"unsafe non-ASCII profile string: {value!r}")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def native_rect(
    values: dict[str, str], rotation_key: str = "rotation"
) -> tuple[int, int, int, int]:
    x = bounded_int(values, "logo_x", 0, 16383)
    y = bounded_int(values, "logo_y", 0, 16383)
    width = bounded_int(values, "logo_width", 1, 16384)
    height = bounded_int(values, "logo_height", 1, 16384)
    canvas_width = bounded_int(values, "canvas_width", 1, 16384)
    canvas_height = bounded_int(values, "canvas_height", 1, 16384)
    rotation = bounded_int(values, rotation_key, 0, 270)
    if rotation == 0:
        return x, y, width, height
    if rotation == 90:
        return y, canvas_width - x - width, height, width
    if rotation == 180:
        return canvas_width - x - width, canvas_height - y - height, width, height
    if rotation == 270:
        return canvas_height - y - height, x, height, width
    raise SystemExit(f"unsupported rotation: {rotation}")


def load_profile(directory: Path, asset_root: str) -> dict:
    cfg = directory / "device.cfg"
    logo = directory / "logo-linux-fbdev.bgra"
    if not cfg.is_file() or not logo.is_file():
        raise SystemExit(
            f"{directory}: device.cfg and logo-linux-fbdev.bgra are required"
        )
    values = parse_cfg(cfg)
    profile_id = values["profile_id"]
    if profile_id != directory.name or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", profile_id):
        raise SystemExit(f"{directory}: profile_id must equal its directory name")
    count = bounded_int(values, "device_tree_compatible_count", 1, MAX_COMPATIBLES)
    compatibles = []
    for index in range(count):
        key = f"device_tree_compatible_{index}"
        value = values.get(key, "")
        if not 3 <= len(value) <= 127 or "\0" in value:
            raise SystemExit(f"{directory}: invalid {key}")
        compatibles.append(value)
    if len(set(compatibles)) != len(compatibles):
        raise SystemExit(f"{directory}: duplicate compatible")

    native_width = bounded_int(values, "native_width", 1, 16384)
    native_height = bounded_int(values, "native_height", 1, 16384)
    bounded_int(values, "native_stride", native_width, 32768)
    if values["pixel_format"] != "bgra8888":
        raise SystemExit(f"{directory}: unsupported firmware framebuffer format")
    linux_width = bounded_int(values, "linux_fbdev_width", 1, 16384)
    linux_height = bounded_int(values, "linux_fbdev_height", 1, 16384)
    linux_stride_bytes = bounded_int(
        values, "linux_fbdev_stride_bytes", linux_width * 4, 131072
    )
    pixel_format = values["linux_fbdev_pixel_format"]
    if pixel_format != "xrgb8888":
        raise SystemExit(f"{directory}: early helper supports only xrgb8888")
    if linux_width != native_width or linux_height != native_height:
        raise SystemExit(f"{directory}: Linux fbdev dimensions must match the panel")
    nx, ny, nw, nh = native_rect(values, "linux_fbdev_rotation")
    if min(nx, ny) < 0 or nx + nw > linux_width or ny + nh > linux_height:
        raise SystemExit(f"{directory}: native logo rectangle is out of bounds")
    expected_payload = nw * nh * 4
    payload_bytes = bounded_int(
        values, "linux_fbdev_logo_payload_bytes", 1, 1 << 30
    )
    logo_bytes = logo.read_bytes()
    if payload_bytes != expected_payload or len(logo_bytes) != 64 + payload_bytes:
        raise SystemExit(f"{directory}: logo payload size mismatch")
    if not re.fullmatch(
        r"[0-9a-f]{64}", values["linux_fbdev_logo_payload_sha256"]
    ):
        raise SystemExit(f"{directory}: malformed logo payload hash")
    magic, header_bytes, logo_width, logo_height, logo_format, header_payload_bytes, header_hash, reserved = struct.unpack(
        "<8sIIIII32sI", logo_bytes[:64]
    )
    payload_hash = hashlib.sha256(logo_bytes[64:]).hexdigest()
    if (
        magic != b"RDKLOGO1"
        or header_bytes != 64
        or logo_width != nw
        or logo_height != nh
        or logo_format != 1
        or header_payload_bytes != payload_bytes
        or reserved != 0
        or header_hash.hex() != payload_hash
        or values["linux_fbdev_logo_payload_sha256"] != payload_hash
    ):
        raise SystemExit(f"{directory}: logo header or payload hash mismatch")
    return {
        "profile_id": profile_id,
        "compatibles": compatibles,
        "native_width": linux_width,
        "native_height": linux_height,
        "native_stride_bytes": linux_stride_bytes,
        "pixel_format": pixel_format,
        "logo_x": nx,
        "logo_y": ny,
        "logo_width": nw,
        "logo_height": nh,
        "logo_path": (
            f"{asset_root}/{profile_id}/"
            "logo-linux-fbdev.bgra"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--asset-root",
        default="/usr/share/consoleos/boot-display/profiles",
    )
    args = parser.parse_args()

    if (
        not args.asset_root.startswith("/")
        or args.asset_root.endswith("/")
        or not args.asset_root.isascii()
        or any(ord(char) < 0x20 for char in args.asset_root)
    ):
        raise SystemExit("asset root must be a normalized absolute ASCII path")

    directories = sorted(path for path in args.profiles.iterdir() if path.is_dir())
    if not directories or len(directories) > MAX_PROFILES:
        raise SystemExit(f"expected 1..{MAX_PROFILES} device profiles")
    profiles = [load_profile(path, args.asset_root) for path in directories]
    owners: dict[str, str] = {}
    for profile in profiles:
        for compatible in profile["compatibles"]:
            previous = owners.setdefault(compatible, profile["profile_id"])
            if previous != profile["profile_id"]:
                raise SystemExit(
                    f"compatible {compatible!r} is ambiguous between {previous} "
                    f"and {profile['profile_id']}"
                )

    lines = [
        "/* Generated. Do not edit. */",
        "#ifndef CONSOLEOS_EARLY_DISPLAY_PROFILES_H",
        "#define CONSOLEOS_EARLY_DISPLAY_PROFILES_H",
        "static const struct boot_display_profile consoleos_display_profiles[] = {",
    ]
    for profile in profiles:
        compatible_values = ", ".join(c_string(value) for value in profile["compatibles"])
        compatible_values += ", NULL" * (MAX_COMPATIBLES - len(profile["compatibles"]))
        lines.extend(
            (
                "\t{",
                f"\t\t.profile_id = {c_string(profile['profile_id'])},",
                f"\t\t.compatibles = {{ {compatible_values} }},",
                f"\t\t.compatible_count = {len(profile['compatibles'])}U,",
                f"\t\t.native_width = {profile['native_width']}U,",
                f"\t\t.native_height = {profile['native_height']}U,",
                f"\t\t.native_stride_bytes = {profile['native_stride_bytes']}U,",
                "\t\t.pixel_format = BOOT_DISPLAY_XRGB8888,",
                f"\t\t.logo_x = {profile['logo_x']}U,",
                f"\t\t.logo_y = {profile['logo_y']}U,",
                f"\t\t.logo_width = {profile['logo_width']}U,",
                f"\t\t.logo_height = {profile['logo_height']}U,",
                f"\t\t.logo_path = {c_string(profile['logo_path'])},",
                "\t},",
            )
        )
    lines.extend(
        (
            "};",
            "#define CONSOLEOS_DISPLAY_PROFILE_COUNT \\",
            "\t(sizeof(consoleos_display_profiles) / sizeof(consoleos_display_profiles[0]))",
            "#endif",
            "",
        )
    )
    args.output.write_text("\n".join(lines), encoding="ascii")


if __name__ == "__main__":
    main()
