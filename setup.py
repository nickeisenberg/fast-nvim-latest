"""Build hook: version and tag the wheel from the Neovim build it carries.

``scripts/fetch_nvim.py`` unpacks one Neovim build into
``src/fast_nvim_release/_nvim/`` and records it in ``_nvim/build.json``: the
package version (``<line>.<k>``, e.g. ``0.12.3``) and the wheel's platform tag.
This file reads both from there, so ``fetch_nvim.py`` then ``pip install .``
(or ``python -m build``) always produces a consistent wheel.
``scripts/check_wheels.py`` verifies the result against ``lines.json``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from setuptools import setup
from setuptools.command.sdist import sdist
from setuptools.dist import Distribution

try:  # setuptools >= 70.1 ships the command itself
    from setuptools.command.bdist_wheel import bdist_wheel
except ImportError:  # pragma: no cover - older toolchains
    from wheel.bdist_wheel import bdist_wheel

BUILD_JSON = Path(__file__).resolve().parent / "src" / "fast_nvim_release" / "_nvim" / "build.json"


def build_info() -> dict:
    try:
        return json.loads(BUILD_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class BinaryDistribution(Distribution):
    """Not pure Python: keeps the package at the wheel root (platlib)."""

    def has_ext_modules(self):
        return True


class PlatformWheel(bdist_wheel):
    def get_tag(self):
        return "py3", "none", build_info().get("tag") or "unknown"

    def run(self):
        if not build_info():
            raise SystemExit(f"no Neovim build in {BUILD_JSON.parent}; run scripts/fetch_nvim.py")
        super().run()


class SourceDist(sdist):
    """An sdist never carries the vendored binaries, only the tracked files."""

    VENDORED = re.compile(r"^src/fast_nvim_release/_nvim/(?!LICENSE\.nvim-[^/]+\.txt$|\.gitkeep$)")

    def make_release_tree(self, base_dir, files):
        kept = [name for name in files if not self.VENDORED.match(name.replace(os.sep, "/"))]
        super().make_release_tree(base_dir, kept)


setup(
    # without a fetched build the version is a placeholder; building a wheel then stops
    version=build_info().get("version") or "0+unvendored",
    cmdclass={"bdist_wheel": PlatformWheel, "sdist": SourceDist},
    distclass=BinaryDistribution,
)
