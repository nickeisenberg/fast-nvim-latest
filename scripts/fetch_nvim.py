#!/usr/bin/env python3
"""Vendor Neovim release archives into ``src/fast_nvim_release/_nvim/``.

Examples
--------
    python scripts/fetch_nvim.py                     # every release, this platform
    python scripts/fetch_nvim.py --release 0.12      # one release line
    python scripts/fetch_nvim.py --platform windows  # cross-vendor for Windows
    python scripts/fetch_nvim.py --all               # every supported platform
    python scripts/fetch_nvim.py --list

Only ``bin/``, ``lib/nvim/`` and ``share/nvim/runtime/`` are kept: those are
what Neovim needs to run, and they let the binary find its runtime relative to
its own path (``../share/nvim/runtime``).

Builds a release lists as ``downloaded_not_bundled`` (0.10 on linux-arm64,
whose glibc floor would break the arm64 wheel tag) are skipped unless
``--include-download-only`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release import bundle  # noqa: E402
from fast_nvim_release.platforms import (  # noqa: E402
    RELEASE_KEYS,
    RELEASES,
    Target,
    get_release,
    host_platform,
    host_target,
    parse_target,
)

DEFAULT_DEST = ROOT / "src" / "fast_nvim_release" / "_nvim"
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


def license_file(dest_root: Path, release_key: str) -> Path:
    """Where one release's copy of Neovim's license lives in the source tree."""
    return dest_root / f"LICENSE.nvim-{release_key}.txt"


def _fetch_licenses(
    dest_root: Path, release_keys: List[str], offline: bool
) -> List[Tuple[str, Path]]:
    """Make sure every vendored release ships its own LICENSE.txt."""
    missing: List[Tuple[str, Path]] = []
    for release_key in release_keys:
        out = license_file(dest_root, release_key)
        if out.is_file():
            continue
        if offline:
            missing.append((release_key, out))
            continue
        url = LICENSE_URL.format(RELEASES[release_key].version)
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": bundle.USER_AGENT}),
                timeout=60,
            ) as response:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(response.read())
        except (urllib.error.URLError, OSError) as exc:
            sys.stderr.write(f"warning: could not fetch {url}: {exc}\n")
            missing.append((release_key, out))
    return missing


def _needs_refresh(final: Path, target: Target) -> Optional[str]:
    """Why an existing vendored copy is out of date, or ``None`` if it is fine.

    Without this, bumping a pinned release and re-running the script would
    keep the old binary while the index claimed the new version.
    """
    try:
        info = json.loads((final / "BUILD_INFO.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "it has no BUILD_INFO.json"
    if not isinstance(info, dict):
        return "its BUILD_INFO.json is not an object"
    if info.get("nvim_version") != target.nvim_version:
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
    final = dest_root / target.release_key / target.key
    exe = final / "bin" / target.exe
    if exe.is_file() and not force:
        stale = _needs_refresh(final, target)
        if stale is None:
            say(f"  {target.label} already vendored ({bundle.human_size(_dir_size(final))})")
            return SKIPPED
        say(f"  re-vendoring {target.label}: {stale}")

    say(f"  fetching {target.asset}")
    try:
        archive = bundle.fetch_archive(target, offline=offline, progress=_progress(target))
    except bundle.FastNvimError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return FAILED

    shown = final.relative_to(ROOT) if final.is_relative_to(ROOT) else final
    say(f"  extracting into {shown}")
    try:
        bundle.extract_archive(target, archive, final)
    except bundle.FastNvimError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return FAILED

    nvim_version: Optional[str] = None
    if target == host_target(target.release_key):
        try:
            nvim_version = bundle.neovim_version(final / "bin" / target.exe)
        except bundle.FastNvimError as exc:
            sys.stderr.write(f"error: vendored binary does not run: {exc}\n")
            return FAILED
        if target.nvim_version not in nvim_version:
            sys.stderr.write(
                f"error: vendored binary reports {nvim_version!r}, expected {target.nvim_version}\n"
            )
            return FAILED

    info = {
        "release": target.release_key,
        "target": target.key,
        "nvim_version": target.nvim_version,
        "asset": target.asset,
        "url": target.url,
        "sha256": target.sha256,
        "archive_size": archive.stat().st_size,
        "installed_size": _dir_size(final),
        "verified": nvim_version,
        "vendored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (final / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    say("  ok {} ({})".format(target.label, bundle.human_size(info["installed_size"])))
    return VENDORED


def write_index(dest_root: Path) -> None:
    releases: Dict[str, object] = {}
    for release_key in RELEASE_KEYS:
        builds: Dict[str, object] = {}
        for target in RELEASES[release_key].targets:
            path = dest_root / release_key / target.key / "BUILD_INFO.json"
            if path.is_file():
                try:
                    builds[target.key] = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
        if builds:
            releases[release_key] = {
                "nvim_version": RELEASES[release_key].version,
                "platforms": builds,
            }
    index = {
        "nvim_versions": {key: RELEASES[key].version for key in RELEASE_KEYS},
        "default_release": RELEASE_KEYS[-1],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "releases": releases,
        "installed_size": _dir_size(dest_root) if dest_root.is_dir() else 0,
    }
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / "BUILD_INFO.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def _platforms_for(release_key: str, dest_root: Path) -> List[str]:
    return [
        t.key
        for t in RELEASES[release_key].targets
        if (dest_root / release_key / t.key / "bin" / t.exe).is_file()
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vendor Neovim release binaries into the fast-nvim-release package."
    )
    parser.add_argument(
        "--release",
        action="append",
        default=[],
        metavar="RELEASE",
        help="release line to vendor (0.10, 0.11, 0.12); repeatable (default: all)",
    )
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        metavar="PLATFORM",
        help="linux/mac/windows or an exact build (linux-arm64); repeatable",
    )
    parser.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    parser.add_argument("--all", action="store_true", help="vendor every platform")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="destination directory")
    parser.add_argument("--force", action="store_true", help="re-vendor even if present")
    parser.add_argument("--offline", action="store_true", help="use cached archives only")
    parser.add_argument("--list", action="store_true", help="list supported releases/platforms")
    parser.add_argument(
        "--include-download-only",
        action="store_true",
        help="also vendor builds that are normally fetched on demand",
    )
    args = parser.parse_args(argv)

    if args.list:
        here = host_platform()
        for release_key in RELEASE_KEYS:
            release = RELEASES[release_key]
            say(f"{release_key} ({release.version})")
            for target in release.targets:
                state = "vendored" if bundle.has_bundled(target) else "-"
                if state != "vendored" and bundle.cached_archive(target).is_file():
                    state = "cached"
                note = release.platform_note(target.key)
                say(
                    "  {:<16} {:<30} {:<9} {}{}".format(
                        target.key,
                        target.asset,
                        state,
                        "host" if here and target.key == here.key else "",
                        f"  [{note}]" if note else "",
                    )
                )
        say(f"\ncache: {bundle.cache_root()}")
        return 0

    dest_root = Path(args.dest).expanduser().resolve()

    release_keys = list(dict.fromkeys(get_release(spec).key for spec in args.release))
    if not release_keys:
        release_keys = list(RELEASE_KEYS)

    targets: List[Target] = []
    for release_key in release_keys:
        wanted = RELEASES[release_key]
        if args.all:
            platform_keys = [t.key for t in wanted.targets]
        elif args.platform:
            # resolve the platform against a release that has every build, then
            # let the per-release loop decide what exists
            platform_keys = [parse_target(spec, args.arch).key for spec in args.platform]
        else:
            here = host_platform()
            if here is None:
                sys.stderr.write("error: cannot detect this platform; pass --platform\n")
                return 1
            platform_keys = [here.key]
        for platform_key in platform_keys:
            target = wanted.target(platform_key)
            if target is None:
                # Every release is vendored by default, so a platform that one
                # line never had (0.10 has no Windows-on-ARM build) must not
                # abort the whole run. Asking for it explicitly still fails.
                if args.release:
                    sys.stderr.write(
                        f"error: Neovim {wanted.version} has no {platform_key} build\n"
                    )
                    return 1
                say(
                    f"  skipping {release_key}/{platform_key}: {wanted.platform_note(platform_key)}"
                )
                continue
            if not wanted.bundled(platform_key) and not args.include_download_only:
                say(f"  skipping {target.label}: {wanted.platform_note(platform_key)}")
                continue
            targets.append(target)

    say(f"fast-nvim-release: vendoring {len(targets)} build(s) into {dest_root}")
    bundle.remove_stale_backups(dest_root)
    results = {}
    for target in targets:
        say(f"{target.label}:")
        results[target.label] = vendor(target, dest_root, offline=args.offline, force=args.force)

    vendored_releases = [key for key in release_keys if _platforms_for(key, dest_root)]
    missing_licenses = _fetch_licenses(dest_root, vendored_releases, args.offline)
    write_index(dest_root)

    say("")
    for release_key in vendored_releases:
        say(
            "vendored {} ({}): {}".format(
                release_key,
                RELEASES[release_key].version,
                ", ".join(_platforms_for(release_key, dest_root)),
            )
        )
    say(f"size on disk: {bundle.human_size(_dir_size(dest_root))}")

    failed = [label for label, result in results.items() if result == FAILED]
    for release_key, path in missing_licenses:
        sys.stderr.write(
            f"error: Neovim's LICENSE.txt for {release_key} is missing\n"
            f"  from {path}; the wheel must ship it (it is fetched from\n"
            f"  {LICENSE_URL.format(RELEASES[release_key].version)}, or drop a copy there)\n"
        )
        failed.append(path.name)
    if failed:
        sys.stderr.write("failed: {}\n".format(", ".join(failed)))
        return 1

    say("next: python -m build --wheel   (tag with FAST_NVIM_RELEASE_PLAT_TAG for cross builds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
