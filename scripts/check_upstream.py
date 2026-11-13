#!/usr/bin/env python3
"""Is every pinned release still the newest patch of its line?

    python scripts/check_upstream.py            # report
    python scripts/check_upstream.py --strict   # exit 1 if a pin is behind

Also mentions Neovim release lines newer than the newest one supported here.
Set ``GITHUB_TOKEN`` to lift the anonymous API rate limit.  (Checksums need no
check here: ``fetch_nvim.py`` verifies them on every build.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import RELEASES  # noqa: E402

API = "https://api.github.com/repos/neovim/neovim/releases?per_page=100&page={}"
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


def fetch_tags() -> list:
    tags = []
    token = os.environ.get("GITHUB_TOKEN")
    for page in range(1, 6):
        request = urllib.request.Request(API.format(page), headers={"User-Agent": "fnr-check"})
        if token:
            request.add_unredirected_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            releases = json.load(response)
        tags += [r["tag_name"] for r in releases if not (r["draft"] or r["prerelease"])]
        if len(releases) < 100:
            return tags
    return tags


def report(newest: Dict[str, Tuple[int, int, int]]) -> list:
    """The lines that are out of date, as messages."""
    stale = []
    for key, release in RELEASES.items():
        latest = newest.get(key)
        tag = "v{}.{}.{}".format(*latest) if latest else "nothing"
        state = "OK" if tag == release.version else "OUTDATED"
        print(f"{key:<6} pinned {release.version:<9} newest {tag:<9} {state}")
        if state != "OK":
            stale.append(f"{key}: pinned {release.version}, Neovim publishes {tag}")
    supported = max(tuple(map(int, key.split("."))) for key in RELEASES)
    for line, version in sorted(newest.items()):
        if tuple(map(int, line.split("."))) > supported:
            stale.append("Neovim has a newer release line: v{}.{}.{}".format(*version))
    return stale


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="exit 1 if anything is behind")
    args = parser.parse_args(argv)
    newest = newest_patches(fetch_tags())
    if not newest:
        print("error: GitHub listed no Neovim releases", file=sys.stderr)
        return 2
    stale = report(newest)
    for item in stale:
        print(f"  - {item}")
    return 1 if stale and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
