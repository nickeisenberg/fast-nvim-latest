#!/usr/bin/env python3
"""Is every pinned release still the newest patch of its line?

    python scripts/check_upstream.py            # report
    python scripts/check_upstream.py --strict   # exit 1 if a pin is behind

Checks both pin tables against the repository each comes from: the main builds
(``neovim/neovim``) and the old-glibc builds (``neovim/neovim-releases``, which
lags).  Also mentions Neovim release lines newer than the newest one supported
here.  Set ``GITHUB_TOKEN`` to lift the anonymous API rate limit.  (Checksums
need no check here: ``fetch_nvim.py`` verifies them on every build.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import OLD_GLIBC_RELEASES, RELEASES, Release  # noqa: E402

API = "https://api.github.com/repos/{}/releases?per_page=100&page={}"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def newest_patches(tags: Iterable[str]) -> Dict[str, Tuple[int, int, int]]:
    """``{"0.12": (0, 12, 6), ...}`` from a list of release tags."""
    newest: Dict[str, Tuple[int, int, int]] = {}
    for tag in tags:
        match = TAG.match(tag)
        if match:
            version = tuple(int(part) for part in match.groups())
            line = f"{version[0]}.{version[1]}"
            newest[line] = max(newest.get(line, version), version)
    return newest


def fetch_tags(repo: str) -> List[str]:
    tags: List[str] = []
    token = os.environ.get("GITHUB_TOKEN")
    for page in range(1, 6):
        request = urllib.request.Request(
            API.format(repo, page), headers={"User-Agent": "fnr-check"}
        )
        if token:
            request.add_unredirected_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            releases = json.load(response)
        tags += [r["tag_name"] for r in releases if not (r["draft"] or r["prerelease"])]
        if len(releases) < 100:
            return tags
    return tags


def _tag(version: Tuple[int, int, int]) -> str:
    return "v{}.{}.{}".format(*version)


def report(newest: Dict[str, Tuple[int, int, int]], pins: Dict[str, Release]) -> List[str]:
    """Print one pin table against upstream; return the pins that are behind."""
    stale = []
    for key, release in pins.items():
        latest = newest.get(key)
        tag = _tag(latest) if latest else "nothing"
        state = "OK" if tag == release.version else "OUTDATED"
        print(f"{key:<6} pinned {release.version:<9} newest {tag:<9} {state}")
        if state != "OK":
            stale.append(f"{key}: pinned {release.version}, {release.repo} publishes {tag}")
    return stale


def newer_lines(newest: Dict[str, Tuple[int, int, int]]) -> List[str]:
    """Neovim release lines newer than every line supported here."""
    supported = max(tuple(map(int, key.split("."))) for key in RELEASES)
    return [
        f"Neovim has a newer release line: {_tag(version)}"
        for line, version in sorted(newest.items())
        if tuple(map(int, line.split("."))) > supported
    ]


def lagging() -> List[str]:
    """Lines whose old-glibc build is an older patch than the main one (expected, not stale)."""
    return [
        f"{key}: the old-glibc wheel ships {old.version}, the others {RELEASES[key].version}"
        for key, old in OLD_GLIBC_RELEASES.items()
        if old.version != RELEASES[key].version
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="exit 1 if anything is behind")
    args = parser.parse_args(argv)
    stale: List[str] = []
    for title, repo, pins in (
        ("main builds", "neovim/neovim", RELEASES),
        ("old-glibc builds", "neovim/neovim-releases", OLD_GLIBC_RELEASES),
    ):
        newest = newest_patches(fetch_tags(repo))
        if not newest:
            print(f"error: GitHub listed no releases for {repo}", file=sys.stderr)
            return 2
        print(f"{title} ({repo})")
        stale += report(newest, pins)
        if pins is RELEASES:
            stale += newer_lines(newest)
        print()
    for item in stale:
        print(f"  - {item}")
    for item in lagging():
        print(f"  note: {item}")
    return 1 if stale and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
