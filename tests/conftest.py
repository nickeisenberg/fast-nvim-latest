"""Test fixtures: the source tree, with a fake bundled Neovim build."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fast_nvim_release import bundle  # noqa: E402
from fast_nvim_release.platforms import Platform, host_platform  # noqa: E402

#: the fake nvim is a shell script, so tests that start it are POSIX-only
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="the fake nvim is a shell script")

FAKE_NVIM = """#!/bin/sh
case "$*" in
  *--version*) echo "NVIM {neovim}" ;;
  *VIMRUNTIME*)
    # like the real binary: the runtime next to us, else a system default
    rt="$(dirname "$0")/../share/nvim/runtime"
    if [ -d "$rt" ]; then printf '%s' "$rt"; else printf '/usr/share/nvim/runtime'; fi ;;
  *) echo "fake nvim: $*" ;;
esac
"""


def make_bundle(root: Path, platform: Platform, neovim: str = "v0.12.6", version="0.12.1"):
    """A tree that looks like the ``_nvim`` directory of a built wheel."""
    exe = root / "bin" / platform.exe
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(FAKE_NVIM.format(neovim=neovim), encoding="utf-8")
    exe.chmod(0o755)
    (root / "lib" / "nvim" / "parser").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "nvim" / "parser" / "c.so").write_bytes(b"parser")
    runtime = root / "share" / "nvim" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "init.lua").write_text(f"-- {neovim}\n", encoding="utf-8")
    info = {
        "version": version,
        "line": version.rsplit(".", 1)[0],
        "neovim": neovim,
        "platform": platform.key,
        "tag": platform.wheel_tag,
    }
    (root / "build.json").write_text(json.dumps(info), encoding="utf-8")
    return root


@pytest.fixture()
def host() -> Platform:
    here = host_platform()
    if here is None:  # pragma: no cover - exotic CI
        pytest.skip("unknown host platform")
    return here


@pytest.fixture()
def fake_bundle(tmp_path, monkeypatch, host) -> Path:
    """The installed package carries a fake Neovim v0.12.6 (package 0.12.1)."""
    root = make_bundle(tmp_path / "_nvim", host)
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    return root


@pytest.fixture()
def prefix(tmp_path) -> Path:
    return tmp_path / "prefix"
