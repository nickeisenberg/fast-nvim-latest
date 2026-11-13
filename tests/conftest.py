"""Test fixtures: run against the source tree, with fake vendored Neovim builds."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fast_nvim_release import bundle  # noqa: E402
from fast_nvim_release.platforms import (  # noqa: E402
    RELEASE_KEYS,
    RELEASES,
    Target,
    host_platform,
)

FAKE_NVIM = """#!/bin/sh
case " $* " in
  *" --version "*)
    echo "NVIM {version}"
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


def make_bundle(root: Path, release_key: str, platform_key: str) -> Path:
    """Create a minimal tree that looks like a vendored Neovim build."""
    target = RELEASES[release_key].target(platform_key)
    assert target is not None, f"{release_key} has no {platform_key} build"
    path = root / release_key / platform_key
    (path / "bin").mkdir(parents=True, exist_ok=True)
    exe = path / "bin" / target.exe
    exe.write_text(FAKE_NVIM.format(version=target.nvim_version[1:]), encoding="utf-8")
    exe.chmod(0o755)
    (path / "lib" / "nvim" / "parser").mkdir(parents=True, exist_ok=True)
    (path / "lib" / "nvim" / "parser" / "c.so").write_bytes(b"\x7fELF fake parser")
    runtime = path / "share" / "nvim" / "runtime"
    (runtime / "syntax").mkdir(parents=True, exist_ok=True)
    (runtime / "syntax" / "syntax.vim").write_text('" fake syntax\n', encoding="utf-8")
    (runtime / "init.lua").write_text("-- fake runtime\n", encoding="utf-8")
    (path / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "release": release_key,
                "target": platform_key,
                "nvim_version": target.nvim_version,
                "sha256": target.sha256,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fake_host() -> str:
    here = host_platform()
    if here is None:  # pragma: no cover - exotic CI
        pytest.skip("unknown host platform")
    return here.key


def build_fake_wheel(tmp_path: Path, releases: List[str]) -> Path:
    """A ``_nvim`` tree with fake builds of ``releases`` for this machine."""
    root = tmp_path / "_nvim"
    root.mkdir()
    platform_key = _fake_host()
    for release_key in releases:
        make_bundle(root, release_key, platform_key)
        (root / f"LICENSE.nvim-{release_key}.txt").write_text(
            "Apache License 2.0\n", encoding="utf-8"
        )
    (root / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "nvim_versions": {key: RELEASES[key].version for key in releases},
                "default_release": releases[-1],
                "releases": {key: {"nvim_version": RELEASES[key].version} for key in releases},
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def fake_bundle(tmp_path, monkeypatch):
    """Point fast-nvim-release at fake vendored builds of *this* platform.

    Every supported release is vendored, like a real wheel.
    """
    root = build_fake_wheel(tmp_path, list(RELEASE_KEYS))
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    monkeypatch.setattr(bundle, "PACKAGE_DIR", tmp_path)
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("FAST_NVIM_RELEASE_PREFIX", raising=False)
    return root


@pytest.fixture()
def host_key() -> str:
    return _fake_host()


@pytest.fixture()
def host_target() -> Target:
    here = host_platform()
    if here is None:  # pragma: no cover - exotic CI
        pytest.skip("unknown host platform")
    target = RELEASES[RELEASE_KEYS[-1]].target(here.key)
    assert target is not None
    return target


@pytest.fixture()
def prefix(tmp_path) -> Path:
    return tmp_path / "prefix"

