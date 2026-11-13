#!/usr/bin/env python3
"""Validate built wheels: one platform's Neovim per wheel, tag matches payload.

    python scripts/check_wheels.py dist/*.whl

Exits non-zero if anything looks wrong, so it can gate a PyPI upload.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim.platforms import NVIM_VERSION, TARGETS  # noqa: E402

WHEEL_NAME = re.compile(r"^fast_nvim-(?P<version>[^-]+)-py3-none-(?P<tag>.+)\.whl$")
BY_TAG = {target.wheel_tag: target for target in TARGETS.values()}


def check(path: Path) -> List[str]:
    problems: List[str] = []
    match = WHEEL_NAME.match(path.name)
    if not match:
        return [
            "unexpected wheel name (want fast_nvim-<version>-py3-none-<platform>.whl)"
        ]
    tag = match.group("tag")
    target = BY_TAG.get(tag)
    if target is None:
        return [
            "unknown platform tag {!r}; known: {}".format(
                tag, ", ".join(sorted(BY_TAG))
            )
        ]

    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        wheel_meta = wheel.read(
            [name for name in names if name.endswith(".dist-info/WHEEL")][0]
        ).decode()
        metadata = wheel.read(
            [name for name in names if name.endswith(".dist-info/METADATA")][0]
        ).decode()

        prefix = "fast_nvim/_nvim/"
        bundles = sorted(
            {
                name[len(prefix) :].split("/")[0]
                for name in names
                if name.startswith(prefix) and name.count("/") > 2
            }
            - {"BUILD_INFO.json", "LICENSE.nvim.txt"}
        )
        if bundles != [target.key]:
            problems.append(
                "expected exactly the {} bundle, found {}".format(target.key, bundles)
            )

        exe = "{}{}/bin/{}".format(prefix, target.key, target.exe)
        if exe not in names:
            problems.append("missing {}".format(exe))
        else:
            mode = wheel.getinfo(exe).external_attr >> 16
            if mode and not mode & 0o111:
                problems.append("{} is not executable in the archive".format(exe))

        runtime = [name for name in names if "/share/nvim/runtime/" in name]
        if len(runtime) < 2000:
            problems.append(
                "only {} runtime files; the wheel is missing $VIMRUNTIME".format(
                    len(runtime)
                )
            )
        for required in ("syntax/syntax.vim", "doc/tags", "filetype.lua"):
            if (
                "{}{}/share/nvim/runtime/{}".format(prefix, target.key, required)
                not in names
            ):
                problems.append("runtime is missing {}".format(required))

        index = "{}{}".format(prefix, "BUILD_INFO.json")
        if index not in names:
            problems.append("missing {}".format(index))
        elif NVIM_VERSION not in wheel.read(index).decode():
            problems.append("{} does not mention {}".format(index, NVIM_VERSION))

        if "Root-Is-Purelib: false" not in wheel_meta:
            problems.append(
                "WHEEL has Root-Is-Purelib: true; data would land in .data/"
            )
        if "Tag: py3-none-{}".format(tag) not in wheel_meta:
            problems.append("WHEEL tag does not match the file name")

        if "Description-Content-Type:" not in metadata:
            problems.append("METADATA has no long description (README)")
        console = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if not console:
            problems.append("missing console_scripts entry point")

        size_mb = path.stat().st_size / 1e6
        if size_mb > 100:
            problems.append(
                "{:.1f} MB exceeds PyPI's 100 MiB per-file limit".format(size_mb)
            )

    return problems


def main(argv: Optional[List[str]] = None) -> int:
    wheels = argv if argv is not None else sys.argv[1:]
    if not wheels:
        wheels = [str(path) for path in sorted((ROOT / "dist").glob("*.whl"))]
    if not wheels:
        print("usage: check_wheels.py [dist/*.whl]", file=sys.stderr)
        return 2

    failures = 0
    print("{:<58} {:>9}  {}".format("wheel", "size", "result"))
    for name in sorted(wheels):
        path = Path(name)
        problems = check(path)
        print(
            "{:<58} {:>7.1f}MB  {}".format(
                path.name, path.stat().st_size / 1e6, "OK" if not problems else "FAILED"
            )
        )
        for problem in problems:
            print("      - {}".format(problem))
        failures += bool(problems)

    print("")
    if failures:
        print("{} of {} wheel(s) failed validation".format(failures, len(wheels)))
        return 1
    print("all {} wheel(s) look good".format(len(wheels)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
