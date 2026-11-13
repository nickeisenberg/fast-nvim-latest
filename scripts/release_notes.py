#!/usr/bin/env python3
"""Create one GitHub release per package version among the given wheels.

    python scripts/release_notes.py dist/*.whl [--dry-run]

Each release is tagged ``v<version>`` and lists, per wheel, the exact Neovim
build inside (the old-glibc wheels can carry an older patch).  Needs ``gh``
with ``GH_TOKEN`` and ``GITHUB_SHA`` set (the release workflow does that).
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List


def notes(wheels: List[Path]) -> Dict[str, str]:
    """version -> release notes (markdown)."""
    entries = collections.defaultdict(list)
    for wheel in sorted(wheels):
        with zipfile.ZipFile(wheel) as zf:
            info = json.loads(zf.read("fast_nvim_release/_nvim/build.json"))
        entries[info["version"]].append(f"- `{info['tag']}`: Neovim {info['neovim']}")
    return {
        version: f'Install: `pip install "fast-nvim-release=={version}"`\n\n'
        + "Wheels:\n"
        + "\n".join(lines)
        + "\n"
        for version, lines in entries.items()
    }


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in args
    wheels = [Path(arg) for arg in args if arg != "--dry-run"]
    for version, body in notes(wheels).items():
        cmd = ["gh", "release", "create", f"v{version}", "--title", f"v{version}", "--notes", body]
        cmd += ["--target", os.environ.get("GITHUB_SHA", "HEAD")]
        if dry_run:
            print(f"--- v{version}\n{body}")
        else:
            subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
