#!/usr/bin/env python3
"""Build the wheels described by ``lines.json``.

    python scripts/build_wheels.py --all
    python scripts/build_wheels.py --line 0.12 --wheel host
    python scripts/build_wheels.py --all --skip-published https://test.pypi.org

Each wheel is built in a throwaway copy of the source tree, so builds never mix
and the repo's own ``_nvim`` directory is left alone.  ``--skip-published``
leaves out every version already on that index: a release publishes exactly
the lines whose ``k`` was bumped.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import List

import lines
from lines import Wheel

from fast_nvim_release.platforms import host_wheel_tag

NVIM = Path("src") / "fast_nvim_release" / "_nvim"
IGNORE = shutil.ignore_patterns(
    "_nvim", ".git", "build", "dist", "*.egg-info", "__pycache__", ".*cache", ".venv", "venv"
)


def published(index: str, version: str) -> bool:
    """True if ``version`` of fast-nvim-release is on ``index`` (a PyPI-like site)."""
    url = f"{index.rstrip('/')}/pypi/fast-nvim-release/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def select(line_keys: List[str], tags: List[str], skip_published: str = "") -> List[Wheel]:
    host = host_wheel_tag()
    wanted_tags = {host if tag == "host" else tag for tag in tags}
    chosen = [
        wheel
        for wheel in lines.wheels()
        if (not line_keys or wheel.line in line_keys) and (not tags or wheel.tag in wanted_tags)
    ]
    if skip_published:
        done = {v for v in {w.version for w in chosen} if published(skip_published, v)}
        for version in sorted(done):
            print(f"skipping {version}: already on {skip_published}")
        chosen = [wheel for wheel in chosen if wheel.version not in done]
    return chosen


def build(wheel: Wheel, outdir: Path) -> Path:
    print(f"==> {wheel.label}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src-tree"
        shutil.copytree(lines.ROOT, work, ignore=IGNORE)
        (work / NVIM).mkdir()
        for tracked in (lines.ROOT / NVIM).glob("LICENSE.nvim-*.txt"):
            shutil.copy2(tracked, work / NVIM / tracked.name)
        fetch = [
            sys.executable,
            "scripts/fetch_nvim.py",
            "--line",
            wheel.line,
            "--wheel",
            wheel.tag,
        ]
        subprocess.run(fetch, cwd=work, check=True)
        built = Path(tmp) / "dist"
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(built)]
        subprocess.run(cmd, cwd=work, check=True, stdout=subprocess.DEVNULL)
        (result,) = built.glob("*.whl")
        outdir.mkdir(parents=True, exist_ok=True)
        return Path(shutil.move(str(result), str(outdir / result.name)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="every line and wheel")
    parser.add_argument("--line", action="append", default=[], help="a line, e.g. 0.12")
    parser.add_argument("--wheel", action="append", default=[], help='a wheel tag, or "host"')
    parser.add_argument("--skip-published", default="", metavar="INDEX_URL")
    parser.add_argument("--outdir", type=Path, default=lines.ROOT / "dist")
    args = parser.parse_args(argv)
    if not (args.all or args.line or args.wheel):
        parser.error("pass --all, or --line/--wheel")
    chosen = select(args.line, args.wheel, args.skip_published)
    if not chosen:
        print("nothing to build")
        return 0
    for wheel in chosen:
        print(f"    {build(wheel, args.outdir).name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
