#!/usr/bin/env python3
"""Vendor Neovim release archives into ``src/fast_nvim/_nvim/<target>/``.

Examples
--------
    python scripts/fetch_nvim.py                     # the platform we're on
    python scripts/fetch_nvim.py --platform windows  # cross-vendor for Windows
    python scripts/fetch_nvim.py --all               # every supported platform
    python scripts/fetch_nvim.py --list

Only ``bin/``, ``lib/nvim/`` and ``share/nvim/runtime/`` are kept: those are
what Neovim needs to run, and they let the binary find its runtime relative to
its own path (``../share/nvim/runtime``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim import bundle
from fast_nvim.platforms import (
    NVIM_VERSION,
    TARGET_KEYS,
    TARGETS,
    Target,
    host_target,
    parse_target,
)

DEFAULT_DEST = ROOT / "src" / "fast_nvim" / "_nvim"
LICENSE_URL = "https://raw.githubusercontent.com/neovim/neovim/{}/LICENSE.txt"


def say(message: str) -> None:
    print(message, flush=True)


def _progress(target: Target):
    state = {"last": -1}

    def report(done: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        percent = int(done * 100 / total) if total else 0
        if percent == state["last"]:
            return
        state["last"] = percent
        sys.stderr.write(
            f"\r  {target.asset} {percent:>3}%  {bundle.human_size(done)}/{bundle.human_size(total)}   "
        )
        sys.stderr.flush()
        if done >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return report


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def _fetch_license(dest: Path, offline: bool) -> Optional[Path]:
    out = dest / "LICENSE.nvim.txt"
    if out.is_file():
        return out
    if offline:
        return None
    url = LICENSE_URL.format(NVIM_VERSION)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": bundle.USER_AGENT}),
            timeout=60,
        ) as response:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(response.read())
        return out
    except (urllib.error.URLError, OSError) as exc:
        sys.stderr.write(f"warning: could not fetch Neovim's LICENSE.txt: {exc}\n")
        return None


def vendor(
    target: Target,
    dest_root: Path,
    *,
    offline: bool = False,
    force: bool = False,
) -> bool:
    final = dest_root / target.key
    exe = final / "bin" / target.exe
    if exe.is_file() and not force:
        say(f"  {target.key} already vendored ({bundle.human_size(_dir_size(final))})")
        return False

    say(f"  fetching {target.asset}")
    try:
        archive = bundle.fetch_archive(
            target, offline=offline, progress=_progress(target)
        )
    except bundle.FastNvimError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return False

    say(
        f"  extracting into {final.relative_to(ROOT) if final.is_relative_to(ROOT) else final}"
    )
    bundle.extract_archive(target, archive, final)

    nvim_version: Optional[str] = None
    if target == host_target():
        try:
            nvim_version = bundle.neovim_version(final / "bin" / target.exe)
        except bundle.FastNvimError as exc:
            sys.stderr.write(f"error: vendored binary does not run: {exc}\n")
            return False
        if NVIM_VERSION not in nvim_version:
            sys.stderr.write(
                f"error: vendored binary reports {nvim_version!r}, expected {NVIM_VERSION}\n"
            )
            return False

    info = {
        "target": target.key,
        "nvim_version": NVIM_VERSION,
        "asset": target.asset,
        "url": target.url,
        "sha256": target.sha256,
        "archive_size": archive.stat().st_size,
        "installed_size": _dir_size(final),
        "verified": nvim_version,
        "vendored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (final / "BUILD_INFO.json").write_text(
        json.dumps(info, indent=2) + "\n", encoding="utf-8"
    )
    say("  ok {} ({})".format(target.key, bundle.human_size(info["installed_size"])))
    return True


def write_index(dest_root: Path) -> None:
    bundles = {}
    for key in TARGET_KEYS:
        path = dest_root / key / "BUILD_INFO.json"
        if path.is_file():
            try:
                bundles[key] = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
    index = {
        "nvim_version": NVIM_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bundles": bundles,
        "installed_size": _dir_size(dest_root) if dest_root.is_dir() else 0,
    }
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / "BUILD_INFO.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the Neovim release binaries into the fast-nvim package."
    )
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        metavar="PLATFORM",
        help="linux/mac/windows or an exact build (linux-arm64); repeatable",
    )
    parser.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    parser.add_argument(
        "--all", action="store_true", help="vendor every supported platform"
    )
    parser.add_argument(
        "--dest", default=str(DEFAULT_DEST), help="destination directory"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-vendor even if present"
    )
    parser.add_argument(
        "--offline", action="store_true", help="use cached archives only"
    )
    parser.add_argument("--list", action="store_true", help="list supported platforms")
    args = parser.parse_args(argv)

    if args.list:
        host = host_target()
        for key in TARGET_KEYS:
            target = TARGETS[key]
            say(
                "{:<16} {:<44} {}{}".format(
                    key,
                    target.asset,
                    "host" if host and key == host.key else "",
                    "  (cached)" if bundle.cached_archive(target).is_file() else "",
                )
            )
        say(f"\nNeovim {NVIM_VERSION}  cache: {bundle.cache_root()}")
        return 0

    dest_root = Path(args.dest).expanduser().resolve()

    if args.all:
        targets = [TARGETS[key] for key in TARGET_KEYS]
    elif args.platform:
        targets = [parse_target(spec, args.arch) for spec in args.platform]
    else:
        targets = [parse_target(None, args.arch)]

    say(f"fast-nvim: vendoring Neovim {NVIM_VERSION} into {dest_root}")
    ok = True
    for target in targets:
        say(f"{target.key}:")
        ok = vendor(target, dest_root, offline=args.offline, force=args.force) and ok

    _fetch_license(dest_root, args.offline)
    write_index(dest_root)

    vendored = [
        key
        for key in TARGET_KEYS
        if (dest_root / key / "bin" / TARGETS[key].exe).is_file()
    ]
    say("")
    say("vendored: {}".format(", ".join(vendored) or "none"))
    say(f"size on disk: {bundle.human_size(_dir_size(dest_root))}")
    say(
        "next: python -m build --wheel   (tag with FAST_NVIM_PLAT_TAG for cross builds)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
