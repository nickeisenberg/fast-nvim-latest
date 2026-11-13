"""Test fixtures: run against the source tree, with a fake vendored Neovim."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fast_nvim_latest import bundle  # noqa: E402
from fast_nvim_latest.platforms import TARGETS, host_target  # noqa: E402

FAKE_NVIM = """#!/bin/sh
case " $* " in
  *" --version "*)
    echo "NVIM v9.9.9"
    echo "fake build for tests"
    exit 0
    ;;
  *"+lua io.write(vim.env.VIMRUNTIME)"*)
    # report the runtime next to us the way the real binary does, and fall
    # back to a system path when it is missing (what real nvim does)
    d=$(dirname "$0")
    rt="$d/../share/nvim/runtime"
    if [ -d "$rt" ]; then
      printf '%s' "$rt"
    else
      printf '/usr/local/share/nvim'
    fi
    exit 0
    ;;
esac
echo "fake nvim: $@"
exit 0
"""


def make_bundle(root: Path, key: str) -> Path:
    """Create a minimal tree that looks like a vendored Neovim build."""
    target = TARGETS[key]
    path = root / key
    (path / "bin").mkdir(parents=True, exist_ok=True)
    exe = path / "bin" / target.exe
    exe.write_text(FAKE_NVIM, encoding="utf-8")
    exe.chmod(0o755)
    (path / "lib" / "nvim" / "parser").mkdir(parents=True, exist_ok=True)
    (path / "lib" / "nvim" / "parser" / "c.so").write_bytes(b"\x7fELF fake parser")
    runtime = path / "share" / "nvim" / "runtime"
    (runtime / "syntax").mkdir(parents=True, exist_ok=True)
    (runtime / "syntax" / "syntax.vim").write_text('" fake syntax\n', encoding="utf-8")
    (runtime / "init.lua").write_text("-- fake runtime\n", encoding="utf-8")
    return path


@pytest.fixture()
def fake_bundle(tmp_path, monkeypatch):
    """Point fast-nvim-latest at a fake vendored build of *this* platform."""
    host = host_target()
    if host is None:  # pragma: no cover - exotic CI
        pytest.skip("unknown host platform")
    root = tmp_path / "_nvim"
    root.mkdir()
    make_bundle(root, host.key)
    (root / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": "v9.9.9", "bundles": {host.key: {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    monkeypatch.setattr(bundle, "PACKAGE_DIR", tmp_path)
    monkeypatch.setenv("FAST_NVIM_LATEST_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("FAST_NVIM_LATEST_PREFIX", raising=False)
    return root


@pytest.fixture()
def prefix(tmp_path) -> Path:
    return tmp_path / "prefix"


@pytest.mark.skipif(os.name == "nt", reason="fake nvim is a POSIX shell script")
def test_fake_bundle_is_runnable(fake_bundle):
    exe = bundle.bundle_exe(host_target().key)
    assert bundle.neovim_version(exe) == "NVIM v9.9.9"
