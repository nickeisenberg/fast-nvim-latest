#!/usr/bin/env python3
"""Build one wheel per platform, each carrying that platform's Neovim releases.

    python scripts/build_wheels.py --all
    python scripts/build_wheels.py --platform linux-arm64 --platform windows-arm64

Each platform is built in a throwaway copy of the source tree, so builds never
mix and the repo's own ``_nvim`` directory is left alone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import PLATFORMS  # noqa: E402

NVIM = Path("src") / "fast_nvim_release" / "_nvim"
IGNORE = shutil.ignore_patterns(
    "_nvim", ".git", "build", "dist", "*.egg-info", "__pycache__", ".*cache", ".venv", "venv"
)


def build(platform_key: str, outdir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src-tree"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        (work / NVIM).mkdir()
        for tracked in (ROOT / NVIM).glob("LICENSE.nvim-*.txt"):
            shutil.copy2(tracked, work / NVIM / tracked.name)

        fetch = [sys.executable, "scripts/fetch_nvim.py", "--platform", platform_key]
        subprocess.run(fetch, cwd=work, check=True)
        env = dict(os.environ, FAST_NVIM_RELEASE_PLAT_TAG=PLATFORMS[platform_key].wheel_tag)
        wheels = Path(tmp) / "dist"
        build_cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels)]
        subprocess.run(build_cmd, cwd=work, env=env, check=True)
        (wheel,) = wheels.glob("*.whl")
        outdir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(wheel), str(outdir / wheel.name)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", action="append", choices=sorted(PLATFORMS), default=[])
    parser.add_argument("--all", action="store_true", help="every platform")
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    platforms = list(PLATFORMS) if args.all else args.platform
    if not platforms:
        parser.error("pass --all or --platform")
    for key in platforms:
        print(f"==> {key}", flush=True)
        print(f"    {build(key, args.outdir).name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
