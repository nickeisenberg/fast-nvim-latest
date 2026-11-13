"""Build hook: tag the wheel with the platform of the vendored Neovim build.

A wheel can only carry one platform's binary.  Every ``fast-nvim`` release
therefore publishes *one wheel per platform* under the same version, and pip
picks the one that matches the machine it is installing on -- the same
approach ``ruff`` and ``uv`` use.

The platform tag is normally derived from the machine doing the build, but it
must match the *vendored* binary, so CI sets ``FAST_NVIM_PLAT_TAG`` when it
builds a wheel for another platform.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from setuptools import setup
from setuptools.dist import Distribution

try:  # setuptools >= 70.1 ships the command itself
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # pragma: no cover - older toolchains
    from wheel.bdist_wheel import bdist_wheel


class BinaryDistribution(Distribution):
    """Tell setuptools this project is not pure Python.

    The "extension modules" are really a vendored executable, but claiming
    them keeps the package files at the root of the wheel (platlib) instead of
    hiding them in ``<name>.data/purelib/``.
    """

    def has_ext_modules(self):  # noqa: D102
        return True


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim.platforms import TARGETS, host_target  # noqa: E402  (stdlib only)

BUNDLE_ROOT = ROOT / "src" / "fast_nvim" / "_nvim"


def platform_tag():
    """Wheel tag for the vendored build (overridable for cross builds).

    Neovim's official Linux builds need glibc >= 2.34; verify with
    ``strings bin/nvim | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1``.
    """
    override = os.environ.get("FAST_NVIM_PLAT_TAG", "").strip()
    if override:
        return override
    host = host_target()
    if host is None:  # pragma: no cover - exotic build host
        raise SystemExit(
            "fast-nvim: don't know how to tag a wheel for {!r}; "
            "set FAST_NVIM_PLAT_TAG".format(sys.platform)
        )
    return host.wheel_tag


def vendored_bundles():
    if not BUNDLE_ROOT.is_dir():
        return []
    return sorted(
        path.name
        for path in BUNDLE_ROOT.iterdir()
        if path.is_dir() and any((path / "bin").glob("nvim*"))
    )


class PlatformWheel(bdist_wheel):
    """A binary wheel tagged for the vendored Neovim build."""

    def get_tag(self):
        return "py3", "none", platform_tag()

    def run(self):
        bundles = vendored_bundles()
        if not bundles and os.environ.get("FAST_NVIM_ALLOW_EMPTY_BIN") != "1":
            raise SystemExit(
                "\nfast-nvim: no vendored Neovim binary found in {}.\n"
                "  Vendor one first:  python scripts/fetch_nvim.py\n"
                "  (or set FAST_NVIM_ALLOW_EMPTY_BIN=1 to build a binary-less wheel)\n".format(
                    BUNDLE_ROOT
                )
            )
        tag = platform_tag()
        if len(bundles) == 1 and bundles[0] != "all":
            expected = {t.wheel_tag: t.key for t in TARGETS.values()}.get(tag)
            if expected and expected != bundles[0]:
                raise SystemExit(
                    "\nfast-nvim: wheel tag {!r} does not match the vendored build {!r}.\n"
                    "  Set FAST_NVIM_PLAT_TAG explicitly if you know what you are doing.\n".format(
                        tag, bundles[0]
                    )
                )
        super().run()


setup(cmdclass={"bdist_wheel": PlatformWheel}, distclass=BinaryDistribution)
