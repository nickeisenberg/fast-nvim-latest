#!/usr/bin/env python3
"""Validate built wheels against ``lines.json`` before they are uploaded.

    python scripts/check_wheels.py [dist/*.whl]

A wheel ``fast_nvim_release-<line>.<k>-py3-none-<tag>.whl`` must carry exactly
the build ``lines.json`` pins for that line and tag (``build.json`` says so),
with its executable, a complete runtime and Neovim's license.  On Linux the
binary's glibc requirement must fit the manylinux tag, or pip would install a
wheel that cannot start.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import lines

WHEEL_NAME = re.compile(
    r"^fast_nvim_release-(?P<line>\d+\.\d+)\.(?P<k>\d+)-py3-none-(?P<tag>.+)\.whl$"
)
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


def check(path: Path, data: Optional[dict] = None) -> List[str]:
    data = data or lines.load()
    match = WHEEL_NAME.match(path.name)
    if not match:
        return [f"unexpected wheel name: {path.name}"]
    line, k, tag = match.group("line"), int(match.group("k")), match.group("tag")
    if line not in data:
        return [f"{line} is not a line in lines.json"]
    if k != data[line]["k"]:
        return [f"version {line}.{k}, but lines.json says {line}.{data[line]['k']}"]
    try:
        spec = lines.find(line, tag, data)
    except KeyError as exc:
        return [exc.args[0]]

    problems = []
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        info_name = f"{PAYLOAD}build.json"
        info = json.loads(wheel.read(info_name)) if info_name in names else {}
        expected = {
            "version": spec.version,
            "line": line,
            "neovim": spec.build.version,
            "platform": spec.platform.key,
            "tag": tag,
        }
        if info != expected:
            problems.append(f"build.json is {info}, expected {expected}")
        exe = f"{PAYLOAD}bin/{spec.platform.exe}"
        runtime = sum(1 for name in names if name.startswith(f"{PAYLOAD}share/nvim/runtime/"))
        if runtime < MIN_RUNTIME_FILES:
            problems.append(f"only {runtime} runtime files")
        if f"{PAYLOAD}LICENSE.nvim-{line}.txt" not in names:
            problems.append(f"missing {PAYLOAD}LICENSE.nvim-{line}.txt")
        if exe not in names:
            problems.append(f"missing {exe}")
        else:
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
        tags_ok = all(f"Tag: py3-none-{part}" in text for part in tag.split("."))
        if "Root-Is-Purelib: false" not in text or not tags_ok:
            problems.append("WHEEL metadata is not a py3-none platform wheel for this tag")
        if not any(n.endswith(".dist-info/entry_points.txt") for n in names):
            problems.append("missing the console script entry point")

    if path.stat().st_size > PYPI_MAX_BYTES:
        problems.append("larger than PyPI's 100 MiB per-file limit")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheels", nargs="*", type=Path, help="default: dist/*.whl")
    args = parser.parse_args(argv)
    wheels = args.wheels or sorted((lines.ROOT / "dist").glob("*.whl"))
    if not wheels:
        parser.error("no wheels given and none in dist/")
    data = lines.load()
    failed = 0
    for wheel in wheels:
        problems = check(wheel, data)
        print(f"{wheel.name}: {'FAILED' if problems else 'OK'}")
        for problem in problems:
            print(f"  - {problem}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
