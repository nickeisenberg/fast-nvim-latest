#!/usr/bin/env python3
"""Validate built wheels before they are uploaded.

    python scripts/check_wheels.py [--expect-version V] [dist/*.whl]

Each wheel must carry, for exactly its tagged platform, every release pinned
for that platform: the executable, a complete runtime and Neovim's license.
On Linux the binaries' glibc requirement must fit the manylinux tag, or pip
would install a wheel that cannot start.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import PLATFORMS, RELEASES  # noqa: E402

WHEEL_NAME = re.compile(r"^fast_nvim_release-(?P<version>[^-]+)-py3-none-(?P<tag>.+)\.whl$")
MANYLINUX = re.compile(r"^manylinux_(\d+)_(\d+)_")
GLIBC_SYMBOL = re.compile(rb"GLIBC_(\d+)\.(\d+)")
PAYLOAD = "fast_nvim_release/_nvim/"
PYPI_MAX_BYTES = 100 * 1024 * 1024
#: 0.10 ships ~1900 runtime files, 0.12 ~2100; far fewer means the runtime is missing
MIN_RUNTIME_FILES = 1500


def glibc_floor(binary: bytes) -> Optional[Tuple[int, int]]:
    """The newest ``GLIBC_x.y`` symbol version an ELF binary requires."""
    found = {(int(a), int(b)) for a, b in GLIBC_SYMBOL.findall(binary)}
    return max(found) if found else None


def carried(names: Set[str]) -> Set[Tuple[str, str]]:
    """``(release, platform)`` pairs with files under ``_nvim/``."""
    pairs = set()
    for name in names:
        parts = name[len(PAYLOAD) :].split("/") if name.startswith(PAYLOAD) else []
        if len(parts) > 2:
            pairs.add((parts[0], parts[1]))
    return pairs


def check(path: Path) -> List[str]:
    match = WHEEL_NAME.match(path.name)
    by_tag = {p.wheel_tag: p for p in PLATFORMS.values()}
    if not match or match.group("tag") not in by_tag:
        return [f"unexpected wheel name or platform tag: {path.name}"]
    tag = match.group("tag")
    platform = by_tag[tag]
    expected = {key for key, release in RELEASES.items() if platform.key in release.sha256}
    problems = []

    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        builds = carried(names)
        if builds != {(key, platform.key) for key in expected}:
            want = ", ".join(sorted(f"{key}/{platform.key}" for key in expected))
            have = ", ".join(sorted(f"{r}/{p}" for r, p in builds)) or "nothing"
            problems.append(f"expected {want}; the wheel carries {have}")

        for release_key in sorted(expected):
            root = f"{PAYLOAD}{release_key}/{platform.key}/"
            exe = f"{root}bin/{platform.exe}"
            licence = f"{PAYLOAD}LICENSE.nvim-{release_key}.txt"
            runtime = sum(1 for name in names if name.startswith(f"{root}share/nvim/runtime/"))
            if licence not in names:
                problems.append(f"missing {licence}")
            if runtime < MIN_RUNTIME_FILES:
                problems.append(f"{root}: only {runtime} runtime files")
            if exe not in names:
                problems.append(f"missing {exe}")
                continue
            mode = wheel.getinfo(exe).external_attr >> 16
            if not exe.endswith(".exe") and mode and not mode & 0o111:
                problems.append(f"{exe} is not executable")
            manylinux = MANYLINUX.match(tag)
            if manylinux:
                promised = (int(manylinux.group(1)), int(manylinux.group(2)))
                needed = glibc_floor(wheel.read(exe))
                if needed is None or needed > promised:
                    problems.append(f"{exe} needs glibc {needed}, the tag promises {promised}")

        meta = next((n for n in names if n.endswith(".dist-info/WHEEL")), None)
        text = wheel.read(meta).decode() if meta else ""
        if "Root-Is-Purelib: false" not in text or f"Tag: py3-none-{tag}" not in text:
            problems.append("WHEEL metadata is not a py3-none platform wheel for this tag")
        if not any(n.endswith(".dist-info/entry_points.txt") for n in names):
            problems.append("missing the console script entry point")

    if path.stat().st_size > PYPI_MAX_BYTES:
        problems.append("larger than PyPI's 100 MiB per-file limit")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheels", nargs="*", type=Path, help="default: dist/*.whl")
    parser.add_argument("--expect-version", help="every wheel must carry this version")
    args = parser.parse_args(argv)
    wheels = args.wheels or sorted((ROOT / "dist").glob("*.whl"))
    if not wheels:
        parser.error("no wheels given and none in dist/")

    failed = 0
    versions = set()
    for wheel in wheels:
        match = WHEEL_NAME.match(wheel.name)
        versions.add(match.group("version") if match else None)
        problems = check(wheel)
        print(f"{wheel.name}: {'FAILED' if problems else 'OK'}")
        for problem in problems:
            print(f"  - {problem}")
        failed += bool(problems)
    if len(versions) > 1:
        print(f"the wheels carry different versions: {sorted(map(str, versions))}")
        failed += 1
    if args.expect_version and versions != {args.expect_version}:
        print(f"expected version {args.expect_version}, found {sorted(map(str, versions))}")
        failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
