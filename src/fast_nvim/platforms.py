"""Known Neovim release targets (platform + architecture) and asset metadata.

A *target* is one downloadable Neovim build, e.g. ``linux-x86_64``.  The
``key`` is what users type on the command line (``fast-nvim setup linux``)
and what names the vendored directory inside the wheel.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

#: Neovim release that is vendored / downloaded by default.
NVIM_VERSION = "v0.12.6"

RELEASES_URL = "https://github.com/neovim/neovim/releases/download"


@dataclass(frozen=True)
class Target:
    key: str  #: canonical name, e.g. ``linux-x86_64``
    family: str  #: ``linux`` | ``macos`` | ``windows``
    arch: str  #: ``x86_64`` | ``arm64``
    asset: str  #: release artifact file name
    archive: str  #: ``tar.gz`` | ``zip``
    strip_prefix: str  #: directory inside the archive that contains bin/ lib/ share/
    sha256: str  #: sha256 of the release artifact
    size: int  #: size of the release artifact in bytes
    exe: str  #: name of the Neovim executable
    wheel_tag: str  #: platform tag used on the wheel that ships this build

    @property
    def url(self) -> str:
        return "{}/{}/{}".format(RELEASES_URL, NVIM_VERSION, self.asset)

    @property
    def is_host(self) -> bool:
        host = host_target()
        return (self.key == host.key) if host else False

    @property
    def human(self) -> str:
        return "{} ({}, {})".format(self.key, self.family, self.arch)


TARGETS: Dict[str, Target] = {
    t.key: t
    for t in (
        Target(
            key="linux-x86_64",
            wheel_tag="manylinux_2_34_x86_64",
            family="linux",
            arch="x86_64",
            asset="nvim-linux-x86_64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-linux-x86_64",
            sha256="474430d53e6264f6d6dd18db42d6dc9df3a1b56ca9e88a325bbf860e1a811d87",
            size=11404582,
            exe="nvim",
        ),
        Target(
            key="linux-arm64",
            wheel_tag="manylinux_2_34_aarch64",
            family="linux",
            arch="arm64",
            asset="nvim-linux-arm64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-linux-arm64",
            sha256="8f1f64a0bdb97247034038c3823c6cbad5bdf9ecd5751b85494b71c3ee04c815",
            size=11367117,
            exe="nvim",
        ),
        Target(
            key="macos-x86_64",
            wheel_tag="macosx_11_0_x86_64",
            family="macos",
            arch="x86_64",
            asset="nvim-macos-x86_64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-macos-x86_64",
            sha256="481424f1dbc85f637d57e47d27d042aa9c883e5a4fe9ee64fc4ee9573b221998",
            size=9844257,
            exe="nvim",
        ),
        Target(
            key="macos-arm64",
            wheel_tag="macosx_11_0_arm64",
            family="macos",
            arch="arm64",
            asset="nvim-macos-arm64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-macos-arm64",
            sha256="1dbd148222b051ba6c307c4d17a318ed083df018a4f6a5a0ce46955ffc9ab87b",
            size=9595887,
            exe="nvim",
        ),
        Target(
            key="windows-x86_64",
            wheel_tag="win_amd64",
            family="windows",
            arch="x86_64",
            asset="nvim-win64.zip",
            archive="zip",
            strip_prefix="nvim-win64",
            sha256="4e12b7103b601ffc8f25954b4da100f339faa73dccb69a5b135e897fa3d893ed",
            size=12624741,
            exe="nvim.exe",
        ),
        Target(
            key="windows-arm64",
            wheel_tag="win_arm64",
            family="windows",
            arch="arm64",
            asset="nvim-win-arm64.zip",
            archive="zip",
            strip_prefix="nvim-win-arm64",
            sha256="2e58d259ffcc6145e5112ea02330126c6b57da09087bb9265fb2fa9aa10b485c",
            size=13188877,
            exe="nvim.exe",
        ),
    )
}

TARGET_KEYS: List[str] = list(TARGETS)

_FAMILY_ALIASES = {
    "linux": "linux",
    "gnu": "linux",
    "gnu-linux": "linux",
    "mac": "macos",
    "macos": "macos",
    "macosx": "macos",
    "osx": "macos",
    "darwin": "macos",
    "win": "windows",
    "windows": "windows",
    "win32": "windows",
    "win64": "windows",
}

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "x86-64": "x86_64",
    "x64": "x86_64",
    "amd64": "x86_64",
    "intel": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "arm": "arm64",
    "apple-silicon": "arm64",
    "silicon": "arm64",
}


def normalize_arch(arch: str) -> str:
    value = (arch or "").strip().lower()
    if value in _ARCH_ALIASES:
        return _ARCH_ALIASES[value]
    raise ValueError(
        "unknown architecture {!r} (try one of: {})".format(
            arch, ", ".join(sorted(set(_ARCH_ALIASES)))
        )
    )


def normalize_family(family: str) -> str:
    value = (family or "").strip().lower()
    if value in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[value]
    raise ValueError(
        "unknown platform {!r} (try one of: {})".format(
            family, ", ".join(sorted(set(_FAMILY_ALIASES)))
        )
    )


def _host_family() -> Optional[str]:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin", "msys"):
        return "windows"
    return None


def _host_arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("arm64", "aarch64", "armv8l"):
        return "arm64"
    return "x86_64"


def host_target() -> Optional[Target]:
    """The Neovim build that runs on the machine we are on, if we know it."""
    family = _host_family()
    if family is None:
        return None
    return TARGETS.get("{}-{}".format(family, _host_arch()))


def _default_arch(family: str) -> str:
    if family == "macos":
        return "arm64"
    return "x86_64"


def parse_target(spec: Optional[str] = None, arch: Optional[str] = None) -> Target:
    """Turn a user supplied ``platform``/``arch`` pair into a :class:`Target`.

    ``spec`` accepts ``linux``, ``mac``, ``macos``, ``windows`` (and aliases),
    optional architecture suffixes (``linux-arm64``, ``windows/amd64``) and
    canonical keys (``linux-x86_64``).  ``None``/``auto``/``host`` means "the
    machine we are running on".
    """
    text = (spec or "").strip().lower()
    if text in ("", "auto", "host", "this", "here"):
        target = host_target()
        if target is None:
            raise ValueError(
                "cannot detect this platform ({} {}); pass one of: {}".format(
                    sys.platform, platform.machine(), ", ".join(TARGET_KEYS)
                )
            )
        if arch and normalize_arch(arch) != target.arch:
            replace_target = TARGETS.get(
                "{}-{}".format(target.family, normalize_arch(arch))
            )
            if replace_target is None:
                raise ValueError("no Neovim build for that platform/arch")
            return replace_target
        return target

    for sep in ("/", ":", "_"):
        text = text.replace(sep, "-")

    family = None
    arch_part = ""
    for alias in sorted(_FAMILY_ALIASES, key=len, reverse=True):
        if text == alias:
            family = _FAMILY_ALIASES[alias]
            break
        if text.startswith(alias + "-"):
            family = _FAMILY_ALIASES[alias]
            arch_part = text[len(alias) + 1 :]
            break
    if family is None:
        family = normalize_family(text.partition("-")[0])
        arch_part = text.partition("-")[2]

    requested_arch = normalize_arch(arch_part) if arch_part else None
    if arch:
        explicit = normalize_arch(arch)
        if requested_arch and requested_arch != explicit:
            raise ValueError(
                "conflicting architectures: {!r} vs --arch {!r}".format(spec, arch)
            )
        requested_arch = explicit

    if requested_arch is None:
        host = host_target()
        candidate_arch = (
            host.arch if host and host.family == family else _default_arch(family)
        )
        if "{}-{}".format(family, candidate_arch) not in TARGETS:
            candidate_arch = _default_arch(family)
    else:
        candidate_arch = requested_arch

    target = TARGETS.get("{}-{}".format(family, candidate_arch))
    if target is None:
        raise ValueError(
            "no Neovim build for that platform; available: {}".format(
                ", ".join(TARGET_KEYS)
            )
        )
    return target
