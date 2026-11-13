"""Test fixtures: the source tree, with fake vendored Neovim builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fast_nvim_release import bundle  # noqa: E402
from fast_nvim_release.platforms import RELEASES, Platform, host_platform  # noqa: E402

#: the fake nvim is a shell script, so tests that start it are POSIX-only
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="the fake nvim is a shell script")

FAKE_NVIM = """#!/bin/sh
case "$*" in
  *--version*) echo "NVIM {version}" ;;
  *VIMRUNTIME*)
    # like the real binary: the runtime next to us, else a system default
    rt="$(dirname "$0")/../share/nvim/runtime"
    if [ -d "$rt" ]; then printf '%s' "$rt"; else printf '/usr/share/nvim/runtime'; fi ;;
  *) echo "fake nvim: $*" ;;
esac
"""


def make_bundle(root: Path, release_key: str, platform: Platform) -> Path:
    """A minimal tree that looks like a vendored Neovim build."""
    tree = root / release_key / platform.key
    exe = tree / "bin" / platform.exe
    exe.parent.mkdir(parents=True)
    exe.write_text(FAKE_NVIM.format(version=RELEASES[release_key].version), encoding="utf-8")
    exe.chmod(0o755)
    (tree / "lib" / "nvim" / "parser").mkdir(parents=True)
    (tree / "lib" / "nvim" / "parser" / "c.so").write_bytes(b"parser")
    runtime = tree / "share" / "nvim" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "init.lua").write_text(f"-- {release_key}\n", encoding="utf-8")
    return tree


@pytest.fixture()
def host() -> Platform:
    here = host_platform()
    if here is None:  # pragma: no cover - exotic CI
        pytest.skip("unknown host platform")
    return here


@pytest.fixture()
def fake_bundle(tmp_path, monkeypatch, host) -> Path:
    """Every release pinned for this platform, vendored as fake builds."""
    root = tmp_path / "_nvim"
    for key, release in RELEASES.items():
        if host.key in release.sha256:
            make_bundle(root, key, host)
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    return root


@pytest.fixture()
def prefix(tmp_path) -> Path:
    return tmp_path / "prefix"
