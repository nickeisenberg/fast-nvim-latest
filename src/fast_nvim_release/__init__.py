"""fast-nvim-release -- ship Neovim releases in a pip wheel.

``pip install fast-nvim-release`` gives you the newest Neovim lines (binary +
runtime) for the platform the wheel was built for, plus older lines on demand;
``fast-nvim-release setup`` puts the release you pick on your PATH.
"""

from __future__ import annotations

__version__ = "0.2.1"

__all__ = ["__version__"]
