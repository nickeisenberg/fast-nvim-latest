"""The Neovim releases and platforms this project ships.

* a **release** is one Neovim minor line pinned to its newest patch
  (``0.12`` -> ``v0.12.6``); users pick it with ``fast-nvim-release setup 0.12``.
* a **platform** is one official build, e.g. ``linux-x86_64``.
* a **wheel** carries one platform's builds of every release.  Linux has two
  wheels per architecture: the official builds (glibc 2.34+) and, for older
  systems such as RHEL 8, the builds Neovim publishes in ``neovim-releases``
  (glibc 2.17).  pip installs the newest one the machine can run.

The checksums are only used when building wheels (``scripts/fetch_nvim.py``);
at runtime the wheel's own contents say what is available.
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
    key: str  #: e.g. ``linux-x86_64``; also the directory name inside the wheel
    asset: str  #: release archive name on GitHub
    exe: str  #: the Neovim executable
    wheel_tag: str  #: platform tag of the wheel that carries this build

    @property
    def archive_dir(self) -> str:
        """The top-level directory inside the release archive."""
        return self.asset.split(".")[0]


@dataclass(frozen=True)
class Release:
    version: str  #: the pinned patch release, e.g. ``v0.12.6``
    sha256: Dict[str, str]  #: platform key -> archive checksum (the builds we ship)
    repo: str = "neovim/neovim"  #: the GitHub repository publishing these builds

    def url(self, platform_key: str) -> str:
        asset = PLATFORMS[platform_key].asset
        return f"https://github.com/{self.repo}/releases/download/{self.version}/{asset}"


@dataclass(frozen=True)
class Wheel:
    """One published wheel: its platform tag and the builds it carries."""

    tag: str  #: e.g. ``manylinux_2_34_x86_64``
    platform: str  #: key into :data:`PLATFORMS` (build directory, archive name)
    releases: Dict[str, Release]  #: release line -> the pinned build it ships


#: ``wheel_tag`` must hold for every release in the wheel: Neovim's official
#: builds need glibc 2.34 and macOS 11.
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

#: Oldest first; the last one is the default.  Checksums come from each
#: release's ``.sha256sum`` file or GitHub's asset digest.
RELEASES: Dict[str, Release] = {
    "0.10": Release(
        "v0.10.4",
        {
            "linux-x86_64": "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f",
            # no linux-arm64: the 0.10 arm64 build needs glibc 2.38, which would
            # raise the whole arm64 wheel above manylinux_2_34
            "macos-x86_64": "c1405071127b59dbdefc31d9c52e9a5c36db67dcef6dcf83e898aada1f3f778e",
            "macos-arm64": "3d7b07ec9b491d2a3d55167bc1db1cfa96773a1a37e74ea384cb15ab0189223b",
            "windows-x86_64": "dceeb8301f64e244e3e2dffaedbb153bd01c0c6ecb5024a90e3172dc8e65555c",
            # no windows-arm64: Neovim had no Windows-on-ARM build before 0.11
        },
    ),
    "0.11": Release(
        "v0.11.7",
        {
            "linux-x86_64": "38a7c6317f94503841096c00e8fde05ef04b9472fc9d7d62b6e033cecd6f7991",
            "linux-arm64": "99bb3c53604e83ce18fc0b459e34cf1a5e212f4e5fbe2eb136b3c18092ae9905",
            "macos-x86_64": "014649c0c75e188fd70d40f787ee7cb83e7c2926264cd4e72c300d0198922371",
            "macos-arm64": "9c84686ce84bbab725ff3d88ca3ac1d6da5a72128b68d9dd8094e34d4049a126",
            "windows-x86_64": "7b10e6e08b14bf256422b72cfe706ef0452fd9c0a40e138426e1af09803d697f",
            "windows-arm64": "8e4fff5c06eee1b1bb6dead9be74da5f7c59b540c3f90409bd17b85971c03b5d",
        },
    ),
    "0.12": Release(
        "v0.12.6",
        {
            "linux-x86_64": "474430d53e6264f6d6dd18db42d6dc9df3a1b56ca9e88a325bbf860e1a811d87",
            "linux-arm64": "8f1f64a0bdb97247034038c3823c6cbad5bdf9ecd5751b85494b71c3ee04c815",
            "macos-x86_64": "481424f1dbc85f637d57e47d27d042aa9c883e5a4fe9ee64fc4ee9573b221998",
            "macos-arm64": "1dbd148222b051ba6c307c4d17a318ed083df018a4f6a5a0ce46955ffc9ab87b",
            "windows-x86_64": "4e12b7103b601ffc8f25954b4da100f339faa73dccb69a5b135e897fa3d893ed",
            "windows-arm64": "2e58d259ffcc6145e5112ea02330126c6b57da09087bb9265fb2fa9aa10b485c",
        },
    ),
}

#: Neovim's builds for older Linux systems (``neovim/neovim-releases``): they
#: need only glibc 2.17, so they run on RHEL/CentOS 7 and 8.  That repository
#: lags the main one, so a line may be pinned to an older patch here.  Linux
#: arm64 builds only exist there from 0.12.3 on.
OLD_GLIBC_RELEASES: Dict[str, Release] = {
    "0.10": Release(
        "v0.10.4",
        # no published checksum (uploaded before GitHub recorded digests and
        # without a .sha256sum file): pinned from two downloads that matched
        {"linux-x86_64": "879c0886f47f026d519dcd7e398d7e0f060c944adf791f58539592f0346aa851"},
        repo="neovim/neovim-releases",
    ),
    "0.11": Release(
        "v0.11.7",
        {"linux-x86_64": "26606949c3688c2c66bc8c1ba6faaa375edbb48091d3b2cd9c9d8b35fb0a478f"},
        repo="neovim/neovim-releases",
    ),
    "0.12": Release(
        "v0.12.5",
        {
            "linux-x86_64": "9e4a0e9c2aebbcf1eb5f6cdf645b460c9249f517650d0191170f9f152ecfd721",
            "linux-arm64": "b59063930b3ec62aed6257a4a388d33939c2d8804cce5723836659f7feef876c",
        },
        repo="neovim/neovim-releases",
    ),
}

DEFAULT_RELEASE: str = list(RELEASES)[-1]


def _wheel(tag: str, platform_key: str, table: Dict[str, Release]) -> Wheel:
    carried = {key: release for key, release in table.items() if platform_key in release.sha256}
    return Wheel(tag, platform_key, carried)


#: Every wheel a release publishes, by platform tag.  The old-glibc tags also
#: name ``manylinux2014``, so pip versions before 20.3 (RHEL 8's python39
#: ships 20.2) still recognise them.
WHEELS: Dict[str, Wheel] = {
    wheel.tag: wheel
    for wheel in (
        *(_wheel(p.wheel_tag, p.key, RELEASES) for p in PLATFORMS.values()),
        _wheel("manylinux_2_17_x86_64.manylinux2014_x86_64", "linux-x86_64", OLD_GLIBC_RELEASES),
        _wheel("manylinux_2_17_aarch64.manylinux2014_aarch64", "linux-arm64", OLD_GLIBC_RELEASES),
    )
}


def parse_release(spec: Optional[str]) -> str:
    """``0.12``, ``v0.12``, ``12``, ``0.12.6``, ``latest`` or ``None`` -> ``"0.12"``."""
    text = (spec or "latest").strip().lower().lstrip("v")
    if text == "latest":
        return DEFAULT_RELEASE
    for key, release in RELEASES.items():
        if text in (key, key.split(".")[1], release.version[1:]):
            return key
    raise ValueError(
        "unknown Neovim release {!r} (supported: {})".format(spec, ", ".join(RELEASES))
    )


def host_platform() -> Optional[Platform]:
    """The build for this interpreter's platform, or ``None`` if there is none.

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


#: Neovim's official Linux builds need this glibc; older systems need the
#: ``manylinux_2_17`` wheel with the builds from ``neovim-releases``.
OFFICIAL_LINUX_GLIBC = (2, 34)


def host_wheel_tag() -> Optional[str]:
    """The wheel pip would install on this machine (``None`` if there is none).

    Like pip, this looks at the glibc too: on a Linux older than the official
    builds need, it is the old-glibc wheel for this architecture.
    """
    here = host_platform()
    if here is None:
        return None
    glibc = host_glibc()
    if here.key.startswith("linux-") and glibc is not None and glibc < OFFICIAL_LINUX_GLIBC:
        for tag, wheel in WHEELS.items():
            if wheel.platform == here.key and tag.startswith("manylinux_2_17_"):
                return tag
    return here.wheel_tag
