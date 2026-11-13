#!/usr/bin/env python3
"""Build one wheel per platform, each carrying exactly one Neovim build.

Usage
-----
    python scripts/build_wheels.py --all          # all six platforms
    python scripts/build_wheels.py --platform windows --platform macos
    python scripts/build_wheels.py --platform linux-arm64 --outdir wheelhouse

The repo's own ``src/fast_nvim/_nvim`` is never touched: every platform is
vendored into a throwaway copy of the source tree, then ``python -m build``
(or ``pip wheel``) runs there with ``FAST_NVIM_PLAT_TAG`` set.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim.platforms import TARGETS, TARGET_KEYS, Target, parse_target  # noqa: E402

IGNORE = shutil.ignore_patterns(
    "_nvim",
    ".git",
    ".github",
    "dist",
    "build",
    "wheelhouse",
    "*.egg-info",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "tests",
)


def build_command(outdir: Path) -> List[str]:
    """Prefer `python -m build`, fall back to `pip wheel`."""
    try:
        # run from a neutral directory: a local "build/" folder would shadow
        # the real package and make this check lie
        with tempfile.TemporaryDirectory() as neutral:
            subprocess.run(
                [sys.executable, "-m", "build", "--version"],
                check=True,
                cwd=neutral,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)]
    except subprocess.CalledProcessError:
        return [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(outdir),
        ]


def build_one(target: Target, outdir: Path, offline: bool) -> Optional[Path]:
    with tempfile.TemporaryDirectory(prefix="fast-nvim-build-") as tmp:
        work = Path(tmp) / "fast-nvim"
        shutil.copytree(ROOT, work, ignore=IGNORE, symlinks=True)

        fetch = [
            sys.executable,
            str(work / "scripts" / "fetch_nvim.py"),
            "--platform",
            target.key,
            "--dest",
            str(work / "src" / "fast_nvim" / "_nvim"),
        ]
        if offline:
            fetch.append("--offline")
        subprocess.run(fetch, check=True, cwd=str(work))

        env = dict(os.environ)
        env["FAST_NVIM_PLAT_TAG"] = target.wheel_tag
        env.setdefault("PYTHONUNBUFFERED", "1")
        subprocess.run(build_command(outdir), check=True, cwd=str(work), env=env)

    built = sorted(outdir.glob("fast_nvim-*-{}.whl".format(target.wheel_tag)))
    return built[-1] if built else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", action="append", default=[], metavar="PLATFORM")
    parser.add_argument(
        "--all", action="store_true", help="build every supported platform"
    )
    parser.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    parser.add_argument(
        "--outdir", default=str(ROOT / "dist"), help="where wheels land"
    )
    parser.add_argument(
        "--offline", action="store_true", help="use cached archives only"
    )
    args = parser.parse_args(argv)

    if args.all:
        targets = [TARGETS[key] for key in TARGET_KEYS]
    elif args.platform:
        targets = [parse_target(spec, args.arch) for spec in args.platform]
    else:
        parser.error("pass --all or --platform")

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    wheels = []
    for target in targets:
        print("==> {} ({})".format(target.key, target.wheel_tag), flush=True)
        wheel = build_one(target, outdir, args.offline)
        if wheel is None:
            print(
                "    failed to build a wheel for {}".format(target.key), file=sys.stderr
            )
            return 1
        wheels.append(wheel)
        print("    {}".format(wheel.name), flush=True)

    print("\n{} wheel(s) in {}:".format(len(wheels), outdir))
    for wheel in wheels:
        print("  {:<58} {:>7.1f} MB".format(wheel.name, wheel.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
