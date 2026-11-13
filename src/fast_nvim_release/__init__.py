"""fast-nvim-release -- Neovim from PyPI.

Each package version carries one Neovim line, ``<line>.<k>`` (``0.12.3`` is
the third release of the 0.12 line): ``pip install "fast-nvim-release==0.12.*"``
gets it, and ``fast-nvim-release setup --prefix DIR`` installs it.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("fast-nvim-release")
except metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"

__all__ = ["__version__"]
