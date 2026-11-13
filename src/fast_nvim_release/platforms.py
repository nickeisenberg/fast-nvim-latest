"""Supported Neovim releases, platforms, and the pinned asset metadata.

Two independent axes:

* a **release** -- one Neovim minor line, pinned to its newest patch
  (``0.12`` -> ``v0.12.6``).  This is what users pick with
  ``fast-nvim-release setup 0.12``.
* a **platform** -- one downloadable build, e.g. ``linux-x86_64``, which is
  also the name of the vendored directory inside the wheel.

A :class:`Target` is one (release, platform) pair: a single artifact with a
pinned ``sha256``.  Everything else is derived from these two tables.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: Every supported release line, oldest first; each is pinned to its newest
#: patch release.  Keys are what users type; the last one is the default.
RELEASE_VERSIONS: Dict[str, str] = {
    "0.10": "v0.10.4",
    "0.11": "v0.11.7",
    "0.12": "v0.12.6",
}

RELEASE_KEYS: List[str] = list(RELEASE_VERSIONS)

#: Used when the user does not name a release.
DEFAULT_RELEASE: str = RELEASE_KEYS[-1]

RELEASES_URL = "https://github.com/neovim/neovim/releases/download"


@dataclass(frozen=True)
class Platform:
    """One downloadable Neovim build, independent of release."""

    key: str  #: canonical name, e.g. ``linux-x86_64``
    family: str  #: ``linux`` | ``macos`` | ``windows``
    arch: str  #: ``x86_64`` | ``arm64``
    asset: str  #: release artifact file name
    archive: str  #: ``tar.gz`` | ``zip``
    strip_prefix: str  #: directory inside the archive holding bin/ lib/ share/
    exe: str  #: name of the Neovim executable
    wheel_tag: str  #: platform tag on the wheel that ships this build

    @property
    def human(self) -> str:
        return f"{self.key} ({self.family}, {self.arch})"


#: The six binaries Neovim publishes for the release lines we support.
#:
#: ``wheel_tag`` is per platform, not per release: one wheel carries every
#: bundled release, so its tag must be true for all of them.  It is the
#: *highest* glibc / macOS floor among the bundled builds -- see
#: ``Release.downloaded_not_bundled`` for the one build that would have made
#: that impossible.
PLATFORMS: Dict[str, Platform] = {
    p.key: p
    for p in (
        Platform(
            key="linux-x86_64",
            wheel_tag="manylinux_2_34_x86_64",
            family="linux",
            arch="x86_64",
            asset="nvim-linux-x86_64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-linux-x86_64",
            exe="nvim",
        ),
        Platform(
            key="linux-arm64",
            wheel_tag="manylinux_2_34_aarch64",
            family="linux",
            arch="arm64",
            asset="nvim-linux-arm64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-linux-arm64",
            exe="nvim",
        ),
        Platform(
            key="macos-x86_64",
            wheel_tag="macosx_11_0_x86_64",
            family="macos",
            arch="x86_64",
            asset="nvim-macos-x86_64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-macos-x86_64",
            exe="nvim",
        ),
        Platform(
            key="macos-arm64",
            wheel_tag="macosx_11_0_arm64",
            family="macos",
            arch="arm64",
            asset="nvim-macos-arm64.tar.gz",
            archive="tar.gz",
            strip_prefix="nvim-macos-arm64",
            exe="nvim",
        ),
        Platform(
            key="windows-x86_64",
            wheel_tag="win_amd64",
            family="windows",
            arch="x86_64",
            asset="nvim-win64.zip",
            archive="zip",
            strip_prefix="nvim-win64",
            exe="nvim.exe",
        ),
        Platform(
            key="windows-arm64",
            wheel_tag="win_arm64",
            family="windows",
            arch="arm64",
            asset="nvim-win-arm64.zip",
            archive="zip",
            strip_prefix="nvim-win-arm64",
            exe="nvim.exe",
        ),
    )
}

PLATFORM_KEYS: List[str] = list(PLATFORMS)


@dataclass(frozen=True)
class Release:
    """One Neovim release line, pinned to a patch release, with its artifacts."""

    key: str  #: ``0.12``
    version: str  #: ``v0.12.6``
    #: platform key -> (sha256, size in bytes)
    assets: Dict[str, Tuple[str, int]]
    #: platforms Neovim never published for this release
    missing: Tuple[str, ...] = ()
    #: platforms that exist but are deliberately not vendored (see module doc)
    downloaded_not_bundled: Tuple[str, ...] = ()
    #: platform key -> the glibc the official build needs, where we know it
    glibc_floors: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.version[1:]

    @property
    def platforms(self) -> List[Platform]:
        return [PLATFORMS[key] for key in PLATFORM_KEYS if key in self.assets]

    def target(self, platform_key: str) -> Optional[Target]:
        asset = self.assets.get(platform_key)
        if asset is None:
            return None
        sha256, size = asset
        return Target(self, PLATFORMS[platform_key], sha256, size)

    @property
    def targets(self) -> List[Target]:
        return [t for t in (self.target(key) for key in PLATFORM_KEYS) if t is not None]

    def bundled(self, platform_key: str) -> bool:
        """True if this release's build for ``platform_key`` may be vendored."""
        return platform_key in self.assets and platform_key not in self.downloaded_not_bundled

    def platform_note(self, platform_key: str) -> Optional[str]:
        """Why ``platform_key`` is missing or needs a download (``None`` if fine)."""
        if platform_key in self.missing:
            return f"Neovim {self.version} has no {platform_key} build"
        if platform_key in self.downloaded_not_bundled:
            floor = self.glibc_floors.get(platform_key)
            if floor:
                return "not bundled (needs glibc {}.{}); fetched on first use".format(*floor)
            return "not bundled (glibc floor); fetched on first use"
        return None


@dataclass(frozen=True)
class Target:
    """One (release, platform) artifact and its pinned checksum."""

    release: Release
    platform: Platform
    sha256: str
    size: int

    @property
    def key(self) -> str:
        return self.platform.key

    @property
    def family(self) -> str:
        return self.platform.family

    @property
    def arch(self) -> str:
        return self.platform.arch

    @property
    def asset(self) -> str:
        return self.platform.asset

    @property
    def archive(self) -> str:
        return self.platform.archive

    @property
    def strip_prefix(self) -> str:
        return self.platform.strip_prefix

    @property
    def exe(self) -> str:
        return self.platform.exe

    @property
    def wheel_tag(self) -> str:
        return self.platform.wheel_tag

    @property
    def min_glibc(self) -> Optional[Tuple[int, int]]:
        """The glibc this build needs, when we know it (``None`` = unknown)."""
        return self.release.glibc_floors.get(self.platform.key)

    @property
    def release_key(self) -> str:
        return self.release.key

    @property
    def nvim_version(self) -> str:
        return self.release.version

    @property
    def url(self) -> str:
        return f"{RELEASES_URL}/{self.release.version}/{self.asset}"

    @property
    def human(self) -> str:
        return f"Neovim {self.release.display} for {self.platform.human}"

    @property
    def label(self) -> str:
        return f"{self.release.key}/{self.key}"


#: ``sha256`` values were taken from each release's own ``.sha256sum`` file or
#: GitHub's published asset digest, and re-checked against a local download.
RELEASES: Dict[str, Release] = {
    "0.10": Release(
        key="0.10",
        version="v0.10.4",
        assets={
            "linux-x86_64": (
                "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f",
                12007371,
            ),
            "linux-arm64": (
                "c819bf47a9878013ba35ceae87125dd170ede4a72844d049fb35f781045872eb",
                11980554,
            ),
            "macos-x86_64": (
                "c1405071127b59dbdefc31d9c52e9a5c36db67dcef6dcf83e898aada1f3f778e",
                9153002,
            ),
            "macos-arm64": (
                "3d7b07ec9b491d2a3d55167bc1db1cfa96773a1a37e74ea384cb15ab0189223b",
                8795297,
            ),
            "windows-x86_64": (
                "dceeb8301f64e244e3e2dffaedbb153bd01c0c6ecb5024a90e3172dc8e65555c",
                12770924,
            ),
        },
        # Neovim had no Windows-on-ARM build before 0.11
        missing=("windows-arm64",),
        # The 0.10 arm64 binary needs glibc 2.38 while 0.11/0.12 need only
        # 2.34.  Vendoring it would push the whole arm64 wheel to
        # manylinux_2_38 and silently drop Ubuntu 22.04 / Debian 12 / RHEL 9,
        # so 0.10 on arm64 is fetched on demand instead -- and refused on the
        # systems it cannot run on (see glibc_problem).
        downloaded_not_bundled=("linux-arm64",),
        glibc_floors={"linux-arm64": (2, 38)},
    ),
    "0.11": Release(
        key="0.11",
        version="v0.11.7",
        assets={
            "linux-x86_64": (
                "38a7c6317f94503841096c00e8fde05ef04b9472fc9d7d62b6e033cecd6f7991",
                10971409,
            ),
            "linux-arm64": (
                "99bb3c53604e83ce18fc0b459e34cf1a5e212f4e5fbe2eb136b3c18092ae9905",
                10920686,
            ),
            "macos-x86_64": (
                "014649c0c75e188fd70d40f787ee7cb83e7c2926264cd4e72c300d0198922371",
                9472191,
            ),
            "macos-arm64": (
                "9c84686ce84bbab725ff3d88ca3ac1d6da5a72128b68d9dd8094e34d4049a126",
                9207742,
            ),
            "windows-x86_64": (
                "7b10e6e08b14bf256422b72cfe706ef0452fd9c0a40e138426e1af09803d697f",
                12256284,
            ),
            "windows-arm64": (
                "8e4fff5c06eee1b1bb6dead9be74da5f7c59b540c3f90409bd17b85971c03b5d",
                12646461,
            ),
        },
    ),
    "0.12": Release(
        key="0.12",
        version="v0.12.6",
        assets={
            "linux-x86_64": (
                "474430d53e6264f6d6dd18db42d6dc9df3a1b56ca9e88a325bbf860e1a811d87",
                11404582,
            ),
            "linux-arm64": (
                "8f1f64a0bdb97247034038c3823c6cbad5bdf9ecd5751b85494b71c3ee04c815",
                11367117,
            ),
            "macos-x86_64": (
                "481424f1dbc85f637d57e47d27d042aa9c883e5a4fe9ee64fc4ee9573b221998",
                9844257,
            ),
            "macos-arm64": (
                "1dbd148222b051ba6c307c4d17a318ed083df018a4f6a5a0ce46955ffc9ab87b",
                9595887,
            ),
            "windows-x86_64": (
                "4e12b7103b601ffc8f25954b4da100f339faa73dccb69a5b135e897fa3d893ed",
                12624741,
            ),
            "windows-arm64": (
                "2e58d259ffcc6145e5112ea02330126c6b57da09087bb9265fb2fa9aa10b485c",
                13188877,
            ),
        },
    ),
}

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


# --------------------------------------------------------------------------
# releases
# --------------------------------------------------------------------------
_RELEASE_ALIASES = {
    "latest": DEFAULT_RELEASE,
    "newest": DEFAULT_RELEASE,
    "new": DEFAULT_RELEASE,
    "current": DEFAULT_RELEASE,
}


def parse_release(spec: Optional[str] = None) -> str:
    """Turn a user supplied release into a key from :data:`RELEASES`.

    Accepts ``0.12``, ``12``, ``012``, ``v0.12.6``, ``0.12.6``, ``latest``, and
    ``None``/``auto`` for the default release.
    """
    text = (spec or "").strip().lower()
    if text in ("", "auto", "host", "default"):
        return DEFAULT_RELEASE
    if text in _RELEASE_ALIASES:
        return _RELEASE_ALIASES[text]
    if text in RELEASES:
        return text
    bare = text[1:] if text.startswith("v") else text
    if bare in RELEASES:
        return bare
    # "12" or "012": the release line ending in that number
    for key in reversed(RELEASE_KEYS):
        if bare in (key.split(".")[-1], key.replace(".", "")):
            return key
    # a full patch version, e.g. 0.12.6
    parts = bare.split(".")
    if len(parts) == 3:
        line = f"{parts[0]}.{parts[1]}"
        if line in RELEASES and RELEASES[line].version == f"v{bare}":
            return line
    raise ValueError(
        "unknown Neovim release {!r} (try one of: {})".format(spec, ", ".join(RELEASE_KEYS))
    )


def looks_like_release(spec: str) -> bool:
    """True if ``spec`` names a release we know (used to disambiguate argv)."""
    try:
        parse_release(spec)
    except ValueError:
        return False
    return True


def get_release(spec: Optional[str] = None) -> Release:
    return RELEASES[parse_release(spec)]


def release_versions() -> List[str]:
    return [RELEASES[key].version for key in RELEASE_KEYS]


# --------------------------------------------------------------------------
# the machine we are on
# --------------------------------------------------------------------------
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


def host_platform() -> Optional[Platform]:
    """The :class:`Platform` that runs on this machine, if we know it."""
    family = _host_family()
    arch = _host_arch()
    if family is None or arch is None:
        return None
    return PLATFORMS.get(f"{family}-{arch}")


def host_target(release_spec: Optional[str] = None) -> Optional[Target]:
    """The build of ``release_spec`` (default: newest) for this machine."""
    here = host_platform()
    if here is None:
        return None
    return get_release(release_spec).target(here.key)


def is_cross(target: Target) -> bool:
    """True if ``target`` is for another platform than the one we run on."""
    here = host_platform()
    return here is None or target.key != here.key


# --------------------------------------------------------------------------
# glibc, for the builds that are fetched on demand
# --------------------------------------------------------------------------
_GLIBC_VERSION = re.compile(r"(\d+)\.(\d+)")


def host_glibc() -> Optional[Tuple[int, int]]:
    """The glibc this system provides, if it is a glibc system we can ask."""
    if _host_family() != "linux":
        return None
    try:
        reported = os.confstr("CS_GNU_LIBC_VERSION")  # e.g. "glibc 2.34"
    except (AttributeError, OSError, ValueError):  # pragma: no cover - musl, odd libc
        return None
    match = _GLIBC_VERSION.search(reported or "")
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _fits_glibc(target: Optional[Target], have: Tuple[int, int]) -> bool:
    if target is None:
        return False
    floor = target.min_glibc
    return floor is None or floor <= have


def glibc_problem(target: Target) -> Optional[str]:
    """Why ``target`` cannot run on this machine, or ``None`` if it can.

    Only the build for *this* platform is checked: another platform's binary
    is installed for another machine, whose glibc we cannot see.  Unknown
    floors or an unreadable glibc are not treated as a problem -- guessing
    would refuse installs that work.
    """
    floor = target.min_glibc
    if floor is None:
        return None
    here = host_platform()
    if here is None or here.key != target.key:
        return None
    have = host_glibc()
    if have is None or have >= floor:
        return None
    return (
        "Neovim {release} for {platform} needs glibc {need}, but this system has "
        "{have}.\n  that build cannot start here; use a newer release line, e.g.\n"
        "  fast-nvim-release setup {other}".format(
            release=target.release.display,
            platform=target.key,
            need="{}.{}".format(*floor),
            have="{}.{}".format(*have),
            other=next(
                (
                    key
                    for key in reversed(RELEASE_KEYS)
                    if _fits_glibc(RELEASES[key].target(here.key), have)
                ),
                DEFAULT_RELEASE,
            ),
        )
    )


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
    install the matching build--an ``x86_64`` Python on an arm64 Mac gets the
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
            "install fast-nvim-release from a native arm64 Python (python.org "
            "universal2 or arm64 Homebrew) to get the arm64 build"
        )
        how = "Rosetta 2"
    else:
        hint = "install fast-nvim-release from an arm64 Python to get the arm64 build"
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


def parse_target(
    spec: Optional[str] = None,
    arch: Optional[str] = None,
    release_spec: Optional[str] = None,
) -> Target:
    """Turn ``platform``/``arch``/``release`` input into a :class:`Target`.

    ``spec`` accepts ``linux``, ``mac``, ``macos``, ``windows`` (and aliases),
    optional architecture suffixes (``linux-arm64``, ``windows/amd64``) and
    canonical keys (``linux-x86_64``).  ``None``/``auto``/``host`` means "the
    machine we are running on".
    """
    wanted = get_release(release_spec)
    text = (spec or "").strip().lower()
    if text in ("", "auto", "host", "this", "here"):
        here = host_platform()
        if here is None:
            raise ValueError(
                "cannot detect this platform ({} {}); pass one of: {}".format(
                    sys.platform, platform.machine(), ", ".join(PLATFORM_KEYS)
                )
            )
        platform_key = here.key
        if arch:
            wanted_arch = normalize_arch(arch)
            if wanted_arch != here.arch:
                platform_key = f"{here.family}-{wanted_arch}"
        return _target_or_fail(wanted, platform_key)

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
        here = host_platform()
        candidate_arch = here.arch if here and here.family == family else _default_arch(family)
        if f"{family}-{candidate_arch}" not in PLATFORMS:
            candidate_arch = _default_arch(family)
    else:
        candidate_arch = requested_arch

    return _target_or_fail(wanted, f"{family}-{candidate_arch}")


def _target_or_fail(wanted: Release, platform_key: str) -> Target:
    target = wanted.target(platform_key)
    if target is not None:
        return target
    if platform_key in PLATFORMS:
        raise ValueError(
            "Neovim {} has no {} build (available: {})".format(
                wanted.version, platform_key, ", ".join(p.key for p in wanted.platforms)
            )
        )
    raise ValueError(
        "no Neovim build for that platform; available: {}".format(", ".join(PLATFORM_KEYS))
    )
