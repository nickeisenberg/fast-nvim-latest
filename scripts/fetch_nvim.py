#!/usr/bin/env python3
"""Vendor Neovim release archives into ``src/fast_nvim_latest/_nvim/<target>/``.

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

from fast_nvim_latest import bundle  # noqa: E402
from fast_nvim_latest.platforms import (  # noqa: E402
    NVIM_VERSION,
    TARGET_KEYS,
    TARGETS,
    Target,
    host_target,
    parse_target,
)

DEFAULT_DEST = ROOT / "src" / "fast_nvim_latest" / "_nvim"
LICENSE_URL = "https://raw.githubusercontent.com/neovim/neovim/{}/LICENSE.txt"

#: What :func:`vendor` did for one target.
VENDORED = "vendored"
SKIPPED = "skipped"
FAILED = "failed"


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
        done_text = bundle.human_size(done)
        total_text = bundle.human_size(total)
        sys.stderr.write(f"\r  {target.asset} {percent:>3}%  {done_text}/{total_text}   ")
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


def _needs_refresh(final: Path, target: Target) -> Optional[str]:
    """Why an existing vendored copy is out of date, or ``None`` if it is fine.

    Without this, bumping ``NVIM_VERSION`` and re-running the script would
    keep the old binary while the index claimed the new version.
    """
    try:
        info = json.loads((final / "BUILD_INFO.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "it has no BUILD_INFO.json"
    if not isinstance(info, dict):
        return "its BUILD_INFO.json is not an object"
    if info.get("nvim_version") != NVIM_VERSION:
        return "it holds {}".format(info.get("nvim_version") or "an unknown build")
    if target.sha256 and info.get("sha256") != target.sha256:
        return "its pinned archive checksum changed"
    return None


def vendor(
    target: Target,
    dest_root: Path,
    *,
    offline: bool = False,
    force: bool = False,
) -> str:
    final = dest_root / target.key
    exe = final / "bin" / target.exe
    if exe.is_file() and not force:
        stale = _needs_refresh(final, target)
        if stale is None:
            say(f"  {target.key} already vendored ({bundle.human_size(_dir_size(final))})")
            return SKIPPED
        say(f"  re-vendoring {target.key}: {stale}")

    say(f"  fetching {target.asset}")
    try:
        archive = bundle.fetch_archive(target, offline=offline, progress=_progress(target))
    except bundle.FastNvimError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return FAILED

    say(f"  extracting into {final.relative_to(ROOT) if final.is_relative_to(ROOT) else final}")
    try:
        bundle.extract_archive(target, archive, final)
    except bundle.FastNvimError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return FAILED

    nvim_version: Optional[str] = None
    if target == host_target():
        try:
            nvim_version = bundle.neovim_version(final / "bin" / target.exe)
        except bundle.FastNvimError as exc:
            sys.stderr.write(f"error: vendored binary does not run: {exc}\n")
            return FAILED
        if NVIM_VERSION not in nvim_version:
            sys.stderr.write(
                f"error: vendored binary reports {nvim_version!r}, expected {NVIM_VERSION}\n"
            )
            return FAILED

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
    (final / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    say("  ok {} ({})".format(target.key, bundle.human_size(info["installed_size"])))
    return VENDORED


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
    (dest_root / "BUILD_INFO.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the Neovim release binaries into the fast-nvim-latest package."
    )
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        metavar="PLATFORM",
        help="linux/mac/windows or an exact build (linux-arm64); repeatable",
    )
    parser.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    parser.add_argument("--all", action="store_true", help="vendor every supported platform")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="destination directory")
    parser.add_argument("--force", action="store_true", help="re-vendor even if present")
    parser.add_argument("--offline", action="store_true", help="use cached archives only")
    parser.add_argument("--list", action="store_true", help="list supported platforms")
    args = parser.parse_args(argv)

    if args.list:
        host = host_target()
        for key in TARGET_KEYS:
            target = TARGETS[key]
            if bundle.has_bundled(target):
                state = "vendored"
            elif bundle.cached_archive(target).is_file():
                state = "cached"
            else:
                state = "-"
            say(
                "{:<16} {:<44} {:<9} {}".format(
                    key,
                    target.asset,
                    state,
                    "host" if host and key == host.key else "",
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

    say(f"fast-nvim-latest: vendoring Neovim {NVIM_VERSION} into {dest_root}")
    bundle.remove_stale_backups(dest_root)
    results = {}
    for target in targets:
        say(f"{target.key}:")
        results[target.key] = vendor(target, dest_root, offline=args.offline, force=args.force)

    license_path = _fetch_license(dest_root, args.offline)
    write_index(dest_root)

    vendored = [
        key for key in TARGET_KEYS if (dest_root / key / "bin" / TARGETS[key].exe).is_file()
    ]
    say("")
    say("vendored: {}".format(", ".join(vendored) or "none"))
    say(f"size on disk: {bundle.human_size(_dir_size(dest_root))}")

    failed = [key for key, result in results.items() if result == FAILED]
    if license_path is None:
        sys.stderr.write(
            "error: Neovim's LICENSE.txt is missing from {}; the wheel must ship it\n"
            "  (it is fetched from {}, or drop a copy there)\n".format(
                dest_root / "LICENSE.nvim.txt", LICENSE_URL.format(NVIM_VERSION)
            )
        )
        failed.append("LICENSE.nvim.txt")
    if failed:
        sys.stderr.write("failed: {}\n".format(", ".join(failed)))
        return 1

    say("next: python -m build --wheel   (tag with FAST_NVIM_LATEST_PLAT_TAG for cross builds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
