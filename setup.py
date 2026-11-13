"""Build hook: tag the wheel with the platform of the vendored Neovim builds.

A wheel can only carry one platform's binaries.  Every ``fast-nvim-release``
release therefore publishes *one wheel per platform* under the same version,
and each of those wheels carries every supported Neovim release line for that
platform -- pip picks the one that matches the machine it is installing on,
the same approach ``ruff`` and ``uv`` use.

The platform tag is normally derived from the machine doing the build, but it
must match the *vendored* binaries, so CI sets ``FAST_NVIM_RELEASE_PLAT_TAG``
when it builds a wheel for another platform.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

from setuptools import setup
from setuptools.command.sdist import sdist
from setuptools.dist import Distribution

try:  # setuptools >= 70.1 ships the command itself
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # pragma: no cover - older toolchains
    from wheel.bdist_wheel import bdist_wheel


class BinaryDistribution(Distribution):
    """Tell setuptools this project is not pure Python.

    The "extension modules" are really vendored executables, but claiming them
    keeps the package files at the root of the wheel (platlib) instead of
    hiding them in ``<name>.data/purelib/``.
    """

    def has_ext_modules(self):
        return True


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import PLATFORMS, RELEASES, host_platform  # noqa: E402

BUNDLE_ROOT = ROOT / "src" / "fast_nvim_release" / "_nvim"

BY_TAG = {platform.wheel_tag: platform for platform in PLATFORMS.values()}


def platform_tag() -> str:
    """Wheel tag for the vendored builds (overridable for cross builds)."""
    override = os.environ.get("FAST_NVIM_RELEASE_PLAT_TAG", "").strip()
    if override:
        return override
    here = host_platform()
    if here is None:  # pragma: no cover - exotic build host
        raise SystemExit(
            "fast-nvim-release: don't know how to tag a wheel for "
            f"{sys.platform!r}; set FAST_NVIM_RELEASE_PLAT_TAG"
        )
    return here.wheel_tag


def vendored_bundles() -> List[Tuple[str, str]]:
    """``(release, platform)`` pairs that actually have a binary on disk.

    The test is exactly the one the runtime uses (``bin/<exe>`` exists, and the
    release is one we ship rather than fetch on demand), so a build cannot
    advertise a bundle that ``fast-nvim-release setup`` would not find.
    """
    found: List[Tuple[str, str]] = []
    if not BUNDLE_ROOT.is_dir():
        return found
    for release_key in RELEASES:
        directory = BUNDLE_ROOT / release_key
        if not directory.is_dir():
            continue
        for target in RELEASES[release_key].targets:
            if not RELEASES[release_key].bundled(target.key):
                continue  # a download-only build must never end up in a wheel
            if (directory / target.key / "bin" / target.exe).is_file():
                found.append((release_key, target.key))
    return found


class PlatformWheel(bdist_wheel):
    """A binary wheel tagged for the vendored Neovim builds."""

    def get_tag(self):
        return "py3", "none", platform_tag()

    def run(self):
        bundles = vendored_bundles()
        if not bundles and os.environ.get("FAST_NVIM_RELEASE_ALLOW_EMPTY_BIN") != "1":
            raise SystemExit(
                f"\nfast-nvim-release: no vendored Neovim binary found in {BUNDLE_ROOT}.\n"
                "  Vendor them first:  python scripts/fetch_nvim.py\n"
                "  (or set FAST_NVIM_RELEASE_ALLOW_EMPTY_BIN=1 to build a binary-less wheel)\n"
            )
        tag = platform_tag()
        expected = BY_TAG.get(tag)
        if expected is not None:
            if bundles and expected.key not in {platform for _, platform in bundles}:
                names = ", ".join(f"{release}/{platform}" for release, platform in bundles)
                if os.environ.get("FAST_NVIM_RELEASE_PLAT_TAG", "").strip():
                    advice = (
                        "  FAST_NVIM_RELEASE_PLAT_TAG asks for that tag, but the vendored "
                        f"build(s) are {names}.\n"
                        "  Vendor the matching one: python scripts/fetch_nvim.py "
                        f"--platform {expected.key}\n"
                    )
                else:
                    advice = (
                        "  Set FAST_NVIM_RELEASE_PLAT_TAG explicitly if you know\n"
                        "  what you are doing.\n"
                    )
                raise SystemExit(
                    f"\nfast-nvim-release: wheel tag {tag!r} does not match the vendored "
                    f"build(s) {names}.\n{advice}"
                )
        for release_key, _ in bundles:
            licence = BUNDLE_ROOT / f"LICENSE.nvim-{release_key}.txt"
            if not licence.is_file():
                raise SystemExit(
                    f"\nfast-nvim-release: {licence} is missing.\n"
                    "  It is tracked in git; restore it or run scripts/fetch_nvim.py\n"
                )
        super().run()


class SourceDist(sdist):
    """An sdist without vendored binaries.

    ``MANIFEST.in`` has to include all of ``_nvim`` so a wheel built from a
    vendored checkout carries the builds, but a source distribution built from
    the same checkout must not quietly bundle every platform's binaries.  Only
    the tracked files (Neovim's licenses and the directory marker) stay.
    """

    VENDORED = re.compile(r"^src/fast_nvim_release/_nvim/(?!LICENSE\.nvim-[^/]+\.txt$|\.gitkeep$)")

    def make_release_tree(self, base_dir, files):
        kept = [name for name in files if not self.VENDORED.match(name.replace(os.sep, "/"))]
        super().make_release_tree(base_dir, kept)


setup(
    cmdclass={"bdist_wheel": PlatformWheel, "sdist": SourceDist},
    distclass=BinaryDistribution,
)
