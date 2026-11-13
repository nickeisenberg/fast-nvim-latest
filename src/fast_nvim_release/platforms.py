"""The platforms Neovim publishes builds for, and which one this machine is.

Each wheel carries one Neovim line (``0.12``) for one platform.  Linux has two
kinds of wheel per architecture: the official builds (glibc 2.34+) and, for
older systems such as RHEL 8, the builds Neovim publishes in
``neovim/neovim-releases`` (glibc 2.17).  pip installs the newest one the
machine can run.

Which Neovim versions exist, and their checksums, is build-time data: see
``lines.json`` and ``scripts/lines.py``.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Platform:
    key: str  #: e.g. ``linux-x86_64``
    asset: str  #: release archive name on GitHub
    exe: str  #: the Neovim executable
    wheel_tag: str  #: platform tag of the wheel with the official build

    @property
    def archive_dir(self) -> str:
        """The top-level directory inside the release archive."""
        return self.asset.split(".")[0]


PLATFORMS: Dict[str, Platform] = {
    p.key: p
    for p in (
        Platform("linux-x86_64", "nvim-linux-x86_64.tar.gz", "nvim", "manylinux_2_34_x86_64"),
        Platform("linux-arm64", "nvim-linux-arm64.tar.gz", "nvim", "manylinux_2_34_aarch64"),
        Platform("macos-x86_64", "nvim-macos-x86_64.tar.gz", "nvim", "macosx_11_0_x86_64"),
        Platform("macos-arm64", "nvim-macos-arm64.tar.gz", "nvim", "macosx_11_0_arm64"),
        Platform("windows-x86_64", "nvim-win64.zip", "nvim.exe", "win_amd64"),
        Platform("windows-arm64", "nvim-win-arm64.zip", "nvim.exe", "win_arm64"),
    )
}

#: Tags of the wheels with the glibc 2.17 builds.  They also name
#: ``manylinux2014`` so that pip before 20.3 (RHEL 8's python39) recognises them.
OLD_GLIBC_TAGS: Dict[str, str] = {
    "linux-x86_64": "manylinux_2_17_x86_64.manylinux2014_x86_64",
    "linux-arm64": "manylinux_2_17_aarch64.manylinux2014_aarch64",
}

#: Neovim's official Linux builds need this glibc.
OFFICIAL_LINUX_GLIBC = (2, 34)


def host_platform() -> Optional[Platform]:
    """The platform of this interpreter, or ``None`` if Neovim has no build for it.

    This follows the *running* interpreter, like pip's choice of wheel does: an
    x86_64 Python under Rosetta 2 gets the x86_64 build.
    """
    if sys.platform.startswith("linux"):
        family = "linux"
    elif sys.platform == "darwin":
        family = "macos"
    elif sys.platform in ("win32", "cygwin", "msys"):
        family = "windows"
    else:
        return None
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        return None
    return PLATFORMS[f"{family}-{arch}"]


def host_glibc() -> Optional[Tuple[int, int]]:
    """This system's glibc version, or ``None`` (not Linux, musl, unknown)."""
    try:
        reported = os.confstr("CS_GNU_LIBC_VERSION")  # e.g. "glibc 2.28"
    except (AttributeError, OSError, ValueError):
        return None
    match = re.search(r"(\d+)\.(\d+)", reported or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def host_wheel_tag() -> Optional[str]:
    """The wheel tag pip would install on this machine (``None`` if there is none).

    Like pip, this looks at the glibc too: a Linux older than the official
    builds need gets the old-glibc wheel.
    """
    here = host_platform()
    if here is None:
        return None
    glibc = host_glibc()
    if here.key in OLD_GLIBC_TAGS and glibc is not None and glibc < OFFICIAL_LINUX_GLIBC:
        return OLD_GLIBC_TAGS[here.key]
    return here.wheel_tag
