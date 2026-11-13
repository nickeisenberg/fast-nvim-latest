"""Build-time view of ``lines.json``: which wheels exist and what each carries.

``lines.json`` lists every supported Neovim line with:

* ``k``: the line's release counter; the package version is ``<line>.<k>``
  (``0.12.3``).  It goes up whenever anything in that line's wheels changes.
* ``official``: the build from ``neovim/neovim`` (version, sha256 per
  platform, and platforms deliberately ``skip``-ped).
* ``old_glibc`` (optional): the glibc 2.17 build from ``neovim/neovim-releases``.

Each (line, wheel tag) pair with a pinned build is one wheel.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS, Platform  # noqa: E402

LINES_FILE = ROOT / "lines.json"
REPOS = {"official": "neovim/neovim", "old_glibc": "neovim/neovim-releases"}


@dataclass(frozen=True)
class Build:
    kind: str  #: ``official`` or ``old_glibc``
    version: str  #: the Neovim release, e.g. ``v0.12.6``
    sha256: Dict[str, str]  #: platform key -> archive checksum

    @property
    def repo(self) -> str:
        return REPOS[self.kind]

    def url(self, platform_key: str) -> str:
        asset = PLATFORMS[platform_key].asset
        return f"https://github.com/{self.repo}/releases/download/{self.version}/{asset}"


@dataclass(frozen=True)
class Wheel:
    line: str  #: e.g. ``0.12``
    version: str  #: the package version, e.g. ``0.12.3``
    tag: str  #: the wheel's platform tag
    platform: Platform
    build: Build

    @property
    def label(self) -> str:
        return f"{self.version} {self.tag}"


def line_key(line: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in line.split("."))


def load(path: Path = LINES_FILE) -> Dict[str, dict]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return {line: data[line] for line in sorted(data, key=line_key)}


def save(data: Dict[str, dict], path: Path = LINES_FILE) -> None:
    ordered = {line: data[line] for line in sorted(data, key=line_key)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def newest_line(data: Optional[Dict[str, dict]] = None) -> str:
    return list(data or load())[-1]


def builds(spec: dict) -> List[Build]:
    return [
        Build(kind, spec[kind]["version"], dict(spec[kind]["sha256"]))
        for kind in ("official", "old_glibc")
        if spec.get(kind)
    ]


def wheels(data: Optional[Dict[str, dict]] = None) -> List[Wheel]:
    """Every wheel ``lines.json`` describes, newest line last."""
    found = []
    for line, spec in (data or load()).items():
        version = f"{line}.{spec['k']}"
        for build in builds(spec):
            for key, platform in PLATFORMS.items():
                if key not in build.sha256:
                    continue
                tag = platform.wheel_tag if build.kind == "official" else OLD_GLIBC_TAGS[key]
                found.append(Wheel(line, version, tag, platform, build))
    return found


def find(line: str, tag: str, data: Optional[Dict[str, dict]] = None) -> Wheel:
    """The wheel for ``line`` with platform ``tag``; ``KeyError`` saying what exists."""
    candidates = [wheel for wheel in wheels(data) if wheel.line == line]
    for wheel in candidates:
        if wheel.tag == tag:
            return wheel
    have = ", ".join(wheel.tag for wheel in candidates) or "none (unknown line)"
    raise KeyError(f"Neovim {line} has no build for {tag} (it has: {have})")
