#!/usr/bin/env python3
"""Validate built wheels: one platform's Neovim per wheel, tag matches payload.

    python scripts/check_wheels.py dist/*.whl

Exits non-zero if anything looks wrong, so it can gate a PyPI upload.  The
checks that matter at *runtime* (not just at install time) are the ones
derived from the binary itself: its architecture and the minimum glibc /
macOS version it needs must match the wheel tag, otherwise pip happily
installs a wheel that cannot start.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_latest.platforms import NVIM_VERSION, TARGETS  # noqa: E402

WHEEL_NAME = re.compile(r"^fast_nvim_latest-(?P<version>[^-]+)-py3-none-(?P<tag>.+)\.whl$")
BY_TAG = {target.wheel_tag: target for target in TARGETS.values()}

MANYLINUX = re.compile(r"^manylinux_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>.+)$")
MACOSX = re.compile(r"^macosx_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>.+)$")
WIN = re.compile(r"^win_(?P<arch>.+)$")
GLIBC_SYMBOL = re.compile(rb"GLIBC_(\d+)\.(\d+)")

PYPI_MAX_BYTES = 100 * 1024 * 1024
MIN_RUNTIME_FILES = 2000

ELF_MACHINES = {62: "x86_64", 183: "arm64"}
PE_MACHINES = {0x8664: "x86_64", 0xAA64: "arm64"}
MACHO_CPUS = {0x01000007: "x86_64", 0x0100000C: "arm64"}


def _version(pair: Tuple[int, int]) -> str:
    return "{}.{}".format(*pair)


# --------------------------------------------------------------------------
# what the wheel tag promises
# --------------------------------------------------------------------------
def tagged_arch(tag: str) -> Optional[str]:
    for pattern in (MANYLINUX, MACOSX, WIN):
        match = pattern.match(tag)
        if match:
            arch = match.group("arch")
            if arch in ("x86_64", "amd64"):
                return "x86_64"
            return "arm64" if arch in ("aarch64", "arm64") else arch
    return None


def tagged_min_version(tag: str) -> Optional[Tuple[str, Tuple[int, int]]]:
    """:returns: ``(kind, (major, minor))`` for manylinux/macosx tags."""
    match = MANYLINUX.match(tag)
    if match:
        return "glibc", (int(match.group("major")), int(match.group("minor")))
    match = MACOSX.match(tag)
    if match:
        return "macos", (int(match.group("major")), int(match.group("minor")))
    return None


# --------------------------------------------------------------------------
# what the binary in the wheel actually needs
# --------------------------------------------------------------------------
def binary_arch(data: bytes) -> Optional[str]:
    if data[:4] == b"\x7fELF" and len(data) > 20:
        return ELF_MACHINES.get(struct.unpack_from("<H", data, 18)[0])
    if data[:2] == b"MZ" and len(data) > 0x40:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset : offset + 4] == b"PE\0\0":
            return PE_MACHINES.get(struct.unpack_from("<H", data, offset + 4)[0])
        return None
    return _macho_arch(data)


def _macho_headers(data: bytes) -> List[int]:
    """Byte offsets of the Mach-O headers (one for thin files, N for fat)."""
    magic = struct.unpack_from(">I", data, 0)[0] if len(data) >= 4 else 0
    if magic in (0xCAFEBABE, 0xCAFEBABF):  # universal binary
        count = struct.unpack_from(">I", data, 4)[0]
        offsets = []
        for index in range(count):
            base = 8 + index * 20
            if magic == 0xCAFEBABF:
                base = 8 + index * 32
                offsets.append(struct.unpack_from(">Q", data, base + 8)[0])
            else:
                offsets.append(struct.unpack_from(">I", data, base + 8)[0])
        return offsets
    return [0]


def _macho_arch(data: bytes) -> Optional[str]:
    try:
        for offset in _macho_headers(data):
            if struct.unpack_from("<I", data, offset)[0] not in (0xFEEDFACE, 0xFEEDFACF):
                continue
            return MACHO_CPUS.get(struct.unpack_from("<I", data, offset + 4)[0])
    except struct.error:  # pragma: no cover - truncated file
        return None
    return None


def macho_min_os(data: bytes) -> Optional[Tuple[int, int]]:
    """Minimum macOS version from the Mach-O load commands."""
    try:
        for offset in _macho_headers(data):
            magic = struct.unpack_from("<I", data, offset)[0]
            if magic == 0xFEEDFACF:
                header = 32
            elif magic == 0xFEEDFACE:
                header = 28
            else:
                continue
            ncmds = struct.unpack_from("<I", data, offset + 16)[0]
            cursor = offset + header
            for _ in range(ncmds):
                cmd, size = struct.unpack_from("<II", data, cursor)
                if cmd == 0x32:  # LC_BUILD_VERSION
                    encoded = struct.unpack_from("<I", data, cursor + 12)[0]
                    return (encoded >> 16, (encoded >> 8) & 0xFF)
                if cmd == 0x24:  # LC_VERSION_MIN_MACOSX
                    encoded = struct.unpack_from("<I", data, cursor + 8)[0]
                    return (encoded >> 16, (encoded >> 8) & 0xFF)
                if size == 0:  # pragma: no cover - malformed load command
                    break
                cursor += size
    except struct.error:  # pragma: no cover - truncated file
        return None
    return None


def glibc_floor(data: bytes) -> Optional[Tuple[int, int]]:
    """Highest ``GLIBC_x.y`` symbol version the binary requires."""
    versions = {(int(major), int(minor)) for major, minor in GLIBC_SYMBOL.findall(data)}
    return max(versions) if versions else None


# --------------------------------------------------------------------------
# the wheel
# --------------------------------------------------------------------------
def _read_metadata(wheel: zipfile.ZipFile, names: List[str], suffix: str) -> str:
    for name in names:
        if name.endswith(suffix):
            return wheel.read(name).decode("utf-8", "replace")
    raise KeyError(suffix)


def _check_contents(path: Path, tag: str, target) -> List[str]:
    problems: List[str] = []
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        wheel_meta = _read_metadata(wheel, names, ".dist-info/WHEEL")
        metadata = _read_metadata(wheel, names, ".dist-info/METADATA")

        prefix = "fast_nvim_latest/_nvim/"
        bundles = sorted(
            {
                name[len(prefix) :].split("/")[0]
                for name in names
                if name.startswith(prefix) and name.count("/") > 2
            }
            - {"BUILD_INFO.json", "LICENSE.nvim.txt"}
        )
        # more than one bundle is allowed (private/offline distributions),
        # but this wheel's own build has to be there and nothing else may be
        # tagged as it.
        if target.key not in bundles:
            problems.append(f"expected the {target.key} bundle, found {bundles}")

        if f"{prefix}LICENSE.nvim.txt" not in names:
            problems.append(f"missing {prefix}LICENSE.nvim.txt (Neovim's license must ship)")

        exe = f"{prefix}{target.key}/bin/{target.exe}"
        binary = b""
        if exe not in names:
            problems.append(f"missing {exe}")
        else:
            info = wheel.getinfo(exe)
            mode = info.external_attr >> 16
            # only POSIX wheels can record an executable bit; a wheel built on
            # Windows has none, and pip's shim does not need one there
            if target.family != "windows" and mode and not mode & 0o111:
                problems.append(f"{exe} is not executable in the archive")
            binary = wheel.read(exe)

        runtime = [name for name in names if "/share/nvim/runtime/" in name]
        if len(runtime) < MIN_RUNTIME_FILES:
            problems.append(f"only {len(runtime)} runtime files; the wheel is missing $VIMRUNTIME")
        for required in ("syntax/syntax.vim", "doc/tags", "filetype.lua"):
            wanted = f"{prefix}{target.key}/share/nvim/runtime/{required}"
            if wanted not in names:
                problems.append(f"runtime is missing {required}")

        index = "{}{}".format(prefix, "BUILD_INFO.json")
        if index not in names:
            problems.append(f"missing {index}")
        elif NVIM_VERSION not in wheel.read(index).decode():
            problems.append(f"{index} does not mention {NVIM_VERSION}")

        payload = f"{prefix}{target.key}/BUILD_INFO.json"
        if payload not in names:
            problems.append(f"missing {payload}")
        else:
            try:
                info = json.loads(wheel.read(payload).decode())
            except ValueError:
                problems.append(f"{payload} is not valid JSON")
            else:
                if info.get("nvim_version") != NVIM_VERSION:
                    problems.append(
                        "{} is for {}, the pin says {}".format(
                            payload, info.get("nvim_version"), NVIM_VERSION
                        )
                    )
                if target.sha256 and info.get("sha256") != target.sha256:
                    problems.append(f"{payload} has a different archive checksum")

        if binary:
            problems.extend(_check_binary(binary, tag, target, exe))

        if "Root-Is-Purelib: false" not in wheel_meta:
            problems.append("WHEEL has Root-Is-Purelib: true; data would land in .data/")
        if f"Tag: py3-none-{tag}" not in wheel_meta:
            problems.append("WHEEL tag does not match the file name")

        if "Description-Content-Type:" not in metadata:
            problems.append("METADATA has no long description (README)")
        if not [name for name in names if name.endswith(".dist-info/entry_points.txt")]:
            problems.append("missing console_scripts entry point")

    if path.stat().st_size > PYPI_MAX_BYTES:
        problems.append(
            f"{path.stat().st_size / 1024 / 1024:.1f} MB exceeds PyPI's 100 MiB per-file limit"
        )
    return problems


def _check_binary(binary: bytes, tag: str, target, exe: str) -> List[str]:
    problems: List[str] = []
    arch = binary_arch(binary)
    if arch and arch != target.arch:
        problems.append(f"{exe} is a {arch} binary but the wheel is tagged {tag}")
    wanted = tagged_min_version(tag)
    if wanted is None:
        return problems
    kind, floor_tag = wanted
    if kind == "glibc":
        floor = glibc_floor(binary)
        if floor is None:
            problems.append(f"could not determine the glibc requirement of {exe}")
        elif floor > floor_tag:
            problems.append(
                f"{exe} needs glibc {_version(floor)} but the tag promises {_version(floor_tag)}"
            )
    else:
        minimum = macho_min_os(binary)
        if minimum is None:
            problems.append(f"could not determine the macOS requirement of {exe}")
        elif minimum > floor_tag:
            problems.append(
                f"{exe} needs macOS {_version(minimum)} but the tag promises {_version(floor_tag)}"
            )
    return problems


def check(path: Path) -> List[str]:
    match = WHEEL_NAME.match(path.name)
    if not match:
        return ["unexpected wheel name (want fast_nvim_latest-<version>-py3-none-<platform>.whl)"]
    tag = match.group("tag")
    target = BY_TAG.get(tag)
    if target is None:
        return ["unknown platform tag {!r}; known: {}".format(tag, ", ".join(sorted(BY_TAG)))]
    try:
        return _check_contents(path, tag, target)
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        return [f"not a readable wheel: {exc}"]


def wheel_version(path: Path) -> Optional[str]:
    match = WHEEL_NAME.match(path.name)
    return match.group("version") if match else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate fast-nvim-latest wheels: one platform's Neovim per wheel, "
        "and a tag that matches the binary inside it.",
    )
    parser.add_argument("wheels", nargs="*", help="wheel files (default: dist/*.whl)")
    parser.add_argument(
        "--expect-version",
        help="also fail unless every wheel carries this version (used by the "
        "release job to compare the wheel against the git tag)",
    )
    args = parser.parse_args(argv)

    wheels = args.wheels or [str(path) for path in sorted((ROOT / "dist").glob("*.whl"))]
    if not wheels:
        print("usage: check_wheels.py [--expect-version V] [dist/*.whl]", file=sys.stderr)
        return 2

    failures = 0
    print("{:<58} {:>9}  {}".format("wheel", "size", "result"))
    for name in sorted(wheels):
        path = Path(name)
        try:
            size = path.stat().st_size / 1e6
            problems = check(path)
        except OSError as exc:
            size = 0.0
            problems = [f"cannot read: {exc}"]
        print("{:<58} {:>7.1f}MB  {}".format(path.name, size, "OK" if not problems else "FAILED"))
        for problem in problems:
            print(f"      - {problem}")
        failures += bool(problems)

    versions = {wheel_version(Path(name)) for name in wheels} - {None}
    if len(versions) > 1:
        print(
            "\nthe wheels carry different versions: {} (a release must be one version)".format(
                ", ".join(sorted(versions))
            )
        )
        failures += 1
    if args.expect_version:
        wrong = [
            Path(name).name for name in wheels if wheel_version(Path(name)) != args.expect_version
        ]
        if wrong:
            print(
                "\nexpected version {}; these do not match: {}".format(
                    args.expect_version, ", ".join(wrong)
                )
            )
            failures += 1

    print("")
    if failures:
        print(f"{failures} of {len(wheels)} wheel(s) failed validation")
        return 1
    print(f"all {len(wheels)} wheel(s) look good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
