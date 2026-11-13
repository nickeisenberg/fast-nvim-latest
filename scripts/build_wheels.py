#!/usr/bin/env python3
"""Build one wheel per platform, each carrying that platform's releases.

Usage
-----
    python scripts/build_wheels.py --all          # all six platforms
    python scripts/build_wheels.py --platform windows --platform macos
    python scripts/build_wheels.py --platform linux-arm64 --outdir wheelhouse

The repo's own ``src/fast_nvim_release/_nvim`` is never touched: every platform
is vendored into a throwaway copy of the source tree, then ``python -m build``
(or ``pip wheel``) runs there with ``FAST_NVIM_RELEASE_PLAT_TAG`` set.  Each
wheel carries every supported Neovim release line for its platform.
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

from fast_nvim_release.platforms import (  # noqa: E402
    PLATFORM_KEYS,
    PLATFORMS,
    Platform,
    parse_target,
)

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
    ".mypy_cache",
    ".ruff_cache",
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


def _wheel_files(outdir: Path) -> set:
    return {path for path in outdir.glob("*.whl")}


def _preserve_tracked_nvim_files(work: Path) -> None:
    """Keep the files in ``_nvim`` that are tracked in git, not vendored.

    ``_nvim`` is skipped when the tree is copied (it holds tens of megabytes of
    binaries), but Neovim's licenses and the directory marker are tracked there
    and a wheel may not be built without them. Copying them here is what makes
    ``--offline`` work: ``fetch_nvim.py --offline`` cannot download them.
    """
    source = ROOT / "src" / "fast_nvim_release" / "_nvim"
    target = work / "src" / "fast_nvim_release" / "_nvim"
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("LICENSE.nvim-*.txt")):
        shutil.copy2(path, target / path.name)
    if (source / ".gitkeep").is_file():
        shutil.copy2(source / ".gitkeep", target / ".gitkeep")


def build_one(platform: Platform, outdir: Path, offline: bool) -> Optional[Path]:
    before = _wheel_files(outdir)
    with tempfile.TemporaryDirectory(prefix="fast-nvim-release-build-") as tmp:
        work = Path(tmp) / "fast-nvim-release"
        shutil.copytree(ROOT, work, ignore=IGNORE, symlinks=True)
        _preserve_tracked_nvim_files(work)

        fetch = [
            sys.executable,
            str(work / "scripts" / "fetch_nvim.py"),
            "--platform",
            platform.key,
            "--dest",
            str(work / "src" / "fast_nvim_release" / "_nvim"),
        ]
        if offline:
            fetch.append("--offline")
        if subprocess.run(fetch, cwd=str(work)).returncode:
            print(f"    could not vendor {platform.key}", file=sys.stderr)
            return None

        env = dict(os.environ)
        env["FAST_NVIM_RELEASE_PLAT_TAG"] = platform.wheel_tag
        env.setdefault("PYTHONUNBUFFERED", "1")
        if subprocess.run(build_command(outdir), cwd=str(work), env=env).returncode:
            print(f"    could not build the {platform.key} wheel", file=sys.stderr)
            return None

    suffix = f"-{platform.wheel_tag}.whl"
    # only the wheel this run produced: a stale one in dist/ may sort higher
    produced = sorted(path for path in _wheel_files(outdir) - before if path.name.endswith(suffix))
    if produced:
        return produced[0]
    # the build overwrote an existing file of the same name
    candidates = sorted(
        (path for path in _wheel_files(outdir) if path.name.endswith(suffix)),
        key=lambda path: path.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", action="append", default=[], metavar="PLATFORM")
    parser.add_argument("--all", action="store_true", help="build every supported platform")
    parser.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    parser.add_argument("--outdir", default=str(ROOT / "dist"), help="where wheels land")
    parser.add_argument("--offline", action="store_true", help="use cached archives only")
    args = parser.parse_args(argv)

    if args.all:
        platforms = [PLATFORMS[key] for key in PLATFORM_KEYS]
    elif args.platform:
        platforms = [parse_target(spec, args.arch).platform for spec in args.platform]
    else:
        parser.error("pass --all or --platform")

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    wheels = []
    for platform in platforms:
        print(f"==> {platform.key} ({platform.wheel_tag})", flush=True)
        wheel = build_one(platform, outdir, args.offline)
        if wheel is None:
            print(f"    failed to build a wheel for {platform.key}", file=sys.stderr)
            return 1
        wheels.append(wheel)
        print(f"    {wheel.name}", flush=True)

    print(f"\n{len(wheels)} wheel(s) in {outdir}:")
    for wheel in wheels:
        print(f"  {wheel.name:<58} {wheel.stat().st_size / 1e6:>7.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
