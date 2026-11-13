#!/usr/bin/env python3
"""Keep ``lines.json`` in step with Neovim's releases, and bump release counters.

    python scripts/pins.py check [--strict]   # is every pin the newest patch?
    python scripts/pins.py update             # move pins to the newest patches
    python scripts/pins.py bump --all         # after changing our installed code
    python scripts/pins.py bump 0.12          # re-release one line

``update`` asks GitHub for both repositories (``neovim/neovim`` for the
official builds, ``neovim/neovim-releases`` for the glibc 2.17 ones), moves
every pin to the newest patch of its line with the checksums GitHub
publishes, adds new release lines, and bumps ``k`` of every line it changed.
Set ``GITHUB_TOKEN`` to lift the anonymous API rate limit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lines

from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS

API = "https://api.github.com/repos/{}/releases?per_page=100&page={}"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA256 = re.compile(r"\b([0-9a-f]{64})\b")
LICENSE_URL = "https://raw.githubusercontent.com/neovim/neovim/{}/LICENSE.txt"
LICENSE_DIR = lines.ROOT / "src" / "fast_nvim_release" / "_nvim"


# --------------------------------------------------------------------------
# asking GitHub
# --------------------------------------------------------------------------
def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "fast-nvim-release-pins"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # unredirected: release downloads redirect to another host
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_releases(repo: str) -> Dict[str, dict]:
    """tag -> release, for the stable releases of ``repo``."""
    found: Dict[str, dict] = {}
    for page in range(1, 6):
        batch = json.loads(_get(API.format(repo, page)))
        for release in batch:
            if not (release["draft"] or release["prerelease"]) and TAG.match(release["tag_name"]):
                found[release["tag_name"]] = release
        if len(batch) < 100:
            break
    return found


def newest_patches(tags) -> Dict[str, str]:
    """line -> newest tag, e.g. ``{"0.12": "v0.12.7"}``."""
    newest: Dict[str, Tuple[int, int, int]] = {}
    for tag in tags:
        match = TAG.match(tag)
        if match:
            version = tuple(int(part) for part in match.groups())
            line = f"{version[0]}.{version[1]}"
            newest[line] = max(newest.get(line, version), version)
    return {line: "v{}.{}.{}".format(*version) for line, version in newest.items()}


def checksums(release: dict, platform_keys) -> Dict[str, str]:
    """platform -> sha256 of its archive in ``release``, from GitHub's digests."""
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    found = {}
    for key in platform_keys:
        asset = assets.get(PLATFORMS[key].asset)
        if asset is None:
            continue
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            found[key] = digest.split(":", 1)[1]
            continue
        sumfile = assets.get(PLATFORMS[key].asset + ".sha256sum")
        match = SHA256.search(_get(sumfile["browser_download_url"]).decode()) if sumfile else None
        if match is None:
            raise SystemExit(f"{release['tag_name']}: no published checksum for {asset['name']}")
        found[key] = match.group(1)
    return found


# --------------------------------------------------------------------------
# the commands
# --------------------------------------------------------------------------
KINDS = {"official": list(PLATFORMS), "old_glibc": list(OLD_GLIBC_TAGS)}


def plan(data: dict, upstream: Dict[str, Dict[str, dict]]) -> List[Tuple[str, str, str]]:
    """``(line, kind, newest tag)`` for every pin that is behind, plus new lines."""
    changes = []
    newest = {kind: newest_patches(upstream[kind]) for kind in KINDS}
    supported = max(lines.line_key(line) for line in data)
    for line in sorted(set(data) | set(newest["official"]), key=lines.line_key):
        if line not in data and lines.line_key(line) < supported:
            continue  # an old line we never supported
        for kind in KINDS:
            tag = newest[kind].get(line)
            pinned = (data.get(line, {}).get(kind) or {}).get("version")
            if tag and tag != pinned:
                changes.append((line, kind, tag))
    return changes


def apply(data: dict, upstream: Dict[str, Dict[str, dict]], changes) -> List[str]:
    """Edit ``data`` in place; return the lines whose ``k`` was bumped."""
    touched = []
    for line, kind, tag in changes:
        spec = data.setdefault(line, {"k": 0})
        skip = (spec.get(kind) or {}).get("skip", {})
        wanted = [key for key in KINDS[kind] if key not in skip]
        sha = checksums(upstream[kind][tag], wanted)
        if not sha:
            continue
        spec[kind] = {"version": tag, "sha256": sha, **({"skip": skip} if skip else {})}
        if line not in touched:
            touched.append(line)
    for line in touched:
        data[line]["k"] += 1
    return touched


def ensure_license(line: str, data: dict) -> None:
    path = LICENSE_DIR / f"LICENSE.nvim-{line}.txt"
    if not path.is_file():
        path.write_bytes(_get(LICENSE_URL.format(data[line]["official"]["version"])))


def lagging(data: dict) -> List[str]:
    return [
        f"{line}: the old-glibc wheels carry {spec['old_glibc']['version']}, "
        f"the others {spec['official']['version']}"
        for line, spec in data.items()
        if spec.get("old_glibc") and spec["old_glibc"]["version"] != spec["official"]["version"]
    ]


def cmd_check(args, data, upstream) -> int:
    changes = plan(data, upstream)
    for line, kind, tag in changes:
        pinned = (data.get(line, {}).get(kind) or {}).get("version", "nothing")
        print(f"behind: {line} {kind} pinned {pinned}, {lines.REPOS[kind]} has {tag}")
    for note in lagging(data):
        print(f"note: {note}")
    if not changes:
        print("every pin is the newest patch")
    return 1 if changes and args.strict else 0


def cmd_update(args, data, upstream) -> int:
    touched = apply(data, upstream, plan(data, upstream))
    for line in touched:
        ensure_license(line, data)
        print(f"{line} -> {line}.{data[line]['k']}")
    lines.save(data, args.file)
    if not touched:
        print("every pin is the newest patch")
    return 0


def cmd_bump(args, data, _upstream) -> int:
    targets = list(data) if args.all else args.lines
    unknown = [line for line in targets if line not in data]
    if unknown or not targets:
        raise SystemExit(f"unknown lines {unknown}; have {list(data)}" if unknown else "nothing")
    for line in targets:
        data[line]["k"] += 1
        print(f"{line} -> {line}.{data[line]['k']}")
    lines.save(data, args.file)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=lines.LINES_FILE, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="report pins that are behind")
    check.add_argument("--strict", action="store_true", help="exit 1 if any pin is behind")
    commands.add_parser("update", help="move pins to the newest patches and bump k")
    bump = commands.add_parser("bump", help="bump k (re-release lines)")
    bump.add_argument("lines", nargs="*")
    bump.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    data = lines.load(args.file)
    upstream: Optional[Dict[str, Dict[str, dict]]] = None
    if args.command != "bump":
        try:
            upstream = {kind: fetch_releases(repo) for kind, repo in lines.REPOS.items()}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"error: cannot ask GitHub: {exc}", file=sys.stderr)
            return 2
    handler = {"check": cmd_check, "update": cmd_update, "bump": cmd_bump}[args.command]
    return handler(args, data, upstream)


if __name__ == "__main__":
    sys.exit(main())
