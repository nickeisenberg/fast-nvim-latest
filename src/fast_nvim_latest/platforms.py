"""Known Neovim release targets (platform + architecture) and asset metadata.

A *target* is one downloadable Neovim build, e.g. ``linux-x86_64``.  The
``key`` is what users type on the command line (``fast-nvim-latest setup linux``)
and what names the vendored directory inside the wheel.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
        return f"{RELEASES_URL}/{NVIM_VERSION}/{self.asset}"

    @property
    def human(self) -> str:
        return f"{self.key} ({self.family}, {self.arch})"


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


def _host_arch() -> Optional[str]:
    """The Neovim architecture matching this machine, if there is one.

    Returning ``None`` for anything else matters: guessing ``x86_64`` would
    install a binary that cannot run and only fail at the very end.
    """
    machine = (platform.machine() or "").strip().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "x86-64", "amd64", "em64t"):
        return "x86_64"
    return None


def host_target() -> Optional[Target]:
    """The Neovim build that runs on the machine we are on, if we know it."""
    family = _host_family()
    arch = _host_arch()
    if family is None or arch is None:
        return None
    return TARGETS.get(f"{family}-{arch}")


# --------------------------------------------------------------------------
# emulated interpreters (Rosetta 2, Windows on ARM)
# --------------------------------------------------------------------------
_ROSETTA: Dict[str, bool] = {}


def _rosetta_active() -> bool:
    """True if this process is translated by Rosetta 2 on Apple silicon."""
    if "value" in _ROSETTA:
        return _ROSETTA["value"]
    active = False
    try:
        result = subprocess.run(
            ["sysctl", "-in", "sysctl.proc_translated"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.returncode == 0 and result.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no sysctl
        active = False
    _ROSETTA["value"] = active
    return active


def emulation() -> Optional[Tuple[str, str]]:
    """``(arch this Python runs as, arch the machine really is)`` when they differ.

    pip picks the wheel from the *running* interpreter's tag, so we must
    install the matching build—an ``x86_64`` Python on an arm64 Mac gets the
    Intel build, and it works, under Rosetta.  That is correct but slow and
    easy to miss, so callers warn about it instead of trying to be clever.
    """
    family = _host_family()
    machine = (platform.machine() or "").strip().lower()
    if family == "macos" and _rosetta_active():
        if machine in ("x86_64", "x86-64", "amd64", "em64t"):
            return "x86_64", "arm64"
        return None
    if family == "windows":
        # an x64 process on ARM64 Windows reports AMD64 + PROCESSOR_ARCHITEW6432
        outer = (os.environ.get("PROCESSOR_ARCHITEW6432") or "").strip().upper()
        if outer == "ARM64" and machine in ("amd64", "x86_64", "x86-64", "em64t"):
            return "x86_64", "arm64"
    return None


def emulation_note() -> Optional[str]:
    """A one-line hint for emulated interpreters, or ``None``."""
    found = emulation()
    if found is None:
        return None
    running, native = found
    if _host_family() == "macos":
        hint = (
            "install fast-nvim-latest from a native arm64 Python (python.org "
            "universal2 or arm64 Homebrew) to get the arm64 build"
        )
        how = "Rosetta 2"
    else:
        hint = "install fast-nvim-latest from an arm64 Python to get the arm64 build"
        how = "emulation"
    return (
        f"this Python runs as {running} on {native} hardware ({how}), so the "
        f"{running} Neovim build is installed instead of the native {native} one "
        f"(works, but slower); {hint}"
    )


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
            replace_target = TARGETS.get(f"{target.family}-{normalize_arch(arch)}")
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
            raise ValueError(f"conflicting architectures: {spec!r} vs --arch {arch!r}")
        requested_arch = explicit

    if requested_arch is None:
        host = host_target()
        candidate_arch = host.arch if host and host.family == family else _default_arch(family)
        if f"{family}-{candidate_arch}" not in TARGETS:
            candidate_arch = _default_arch(family)
    else:
        candidate_arch = requested_arch

    target = TARGETS.get(f"{family}-{candidate_arch}")
    if target is None:
        raise ValueError(
            "no Neovim build for that platform; available: {}".format(", ".join(TARGET_KEYS))
        )
    return target
