"""Build hook: tag the wheel with the platform of the vendored Neovim builds.

A wheel can only carry one platform's binaries, so every release publishes one
wheel per platform (like ``ruff`` and ``uv``).  The tag comes from
``FAST_NVIM_RELEASE_PLAT_TAG`` (``scripts/build_wheels.py`` sets it) or else
from the build machine; ``scripts/check_wheels.py`` verifies it against the
binaries inside.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.sdist import sdist
from setuptools.dist import Distribution

try:  # setuptools >= 70.1 ships the command itself
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # pragma: no cover - older toolchains
    from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import host_platform  # noqa: E402

BUNDLE_ROOT = ROOT / "src" / "fast_nvim_release" / "_nvim"


class BinaryDistribution(Distribution):
    """Not pure Python: keeps the package at the wheel root (platlib)."""

    def has_ext_modules(self):
        return True


class PlatformWheel(bdist_wheel):
    def get_tag(self):
        tag = os.environ.get("FAST_NVIM_RELEASE_PLAT_TAG", "").strip()
        if not tag:
            here = host_platform()
            if here is None:
                raise SystemExit("unknown build platform; set FAST_NVIM_RELEASE_PLAT_TAG")
            tag = here.wheel_tag
        return "py3", "none", tag

    def run(self):
        if not any(BUNDLE_ROOT.glob("*/*/bin/nvim*")):
            raise SystemExit(f"no Neovim in {BUNDLE_ROOT}; run scripts/fetch_nvim.py first")
        super().run()


class SourceDist(sdist):
    """An sdist never carries the vendored binaries, only the tracked files."""

    VENDORED = re.compile(r"^src/fast_nvim_release/_nvim/(?!LICENSE\.nvim-[^/]+\.txt$|\.gitkeep$)")

    def make_release_tree(self, base_dir, files):
        kept = [name for name in files if not self.VENDORED.match(name.replace(os.sep, "/"))]
        super().make_release_tree(base_dir, kept)


setup(
    cmdclass={"bdist_wheel": PlatformWheel, "sdist": SourceDist},
    distclass=BinaryDistribution,
)
