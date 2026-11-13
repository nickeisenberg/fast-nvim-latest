"""Installing the bundled build into a prefix."""

from __future__ import annotations

import json
import os

import pytest
from conftest import POSIX_ONLY, make_bundle

from fast_nvim_release import bundle


def runtime_text(prefix):
    return (prefix / "share" / "nvim" / "runtime" / "init.lua").read_text(encoding="utf-8")


def test_build_info_and_exe(fake_bundle, host):
    info = bundle.build_info()
    assert info["neovim"] == "v0.12.6" and info["line"] == "0.12"
    assert bundle.bundled_exe() == fake_bundle / "bin" / host.exe


def test_a_package_built_without_neovim_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", tmp_path / "empty")
    assert bundle.build_info() == {}
    with pytest.raises(bundle.FastNvimError, match=r"fetch_nvim\.py"):
        bundle.bundled_exe()


def test_install_copies_the_tree_and_records_it(fake_bundle, host, prefix):
    exe = bundle.install(prefix)
    assert exe == prefix / "bin" / host.exe and exe.is_file()
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
    assert (prefix / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert runtime_text(prefix) == "-- v0.12.6\n"
    info = bundle.installed(prefix)
    assert info["line"] == "0.12" and info["nvim_version"] == "v0.12.6"
    assert info["bin"] == [host.exe]


def test_another_package_version_replaces_the_build_and_keeps_unrelated_files(
    fake_bundle, host, prefix
):
    (fake_bundle / "bin" / "helper").write_text("x", encoding="utf-8")
    bundle.install(prefix)  # owns bin/helper too
    (prefix / "bin" / "other-tool").write_text("mine", encoding="utf-8")

    # what `pip install "fast-nvim-release==0.11.*"` then `setup` looks like
    (fake_bundle / "bin" / "helper").unlink()
    make_bundle(fake_bundle, host, neovim="v0.11.7", version="0.11.1")
    bundle.install(prefix)
    assert runtime_text(prefix) == "-- v0.11.7\n"
    assert not (prefix / "bin" / "helper").exists()
    assert (prefix / "bin" / "other-tool").read_text(encoding="utf-8") == "mine"
    assert bundle.installed(prefix)["line"] == "0.11"


def test_a_stale_runtime_file_does_not_survive_a_reinstall(fake_bundle, prefix):
    bundle.install(prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text("old", encoding="utf-8")
    bundle.install(prefix)
    assert not stale.exists()


@pytest.mark.parametrize("existing", ["bin/{exe}", "bin/helper", "lib/nvim", "share/nvim/runtime"])
def test_files_this_tool_did_not_install_are_never_overwritten(fake_bundle, host, prefix, existing):
    (fake_bundle / "bin" / "helper").write_text("ours", encoding="utf-8")
    target = prefix / existing.format(exe=host.exe)
    target.parent.mkdir(parents=True)
    if existing.startswith("bin/"):
        target.write_text("mine", encoding="utf-8")
    else:
        target.mkdir()
    with pytest.raises(bundle.FastNvimError, match="--force") as excinfo:
        bundle.install(prefix)
    assert str(target) in str(excinfo.value)
    assert not bundle.installed(prefix)
    bundle.install(prefix, force=True)
    assert bundle.installed(prefix)["line"] == "0.12"


def test_a_managed_prefix_still_protects_bin_entries_it_never_owned(fake_bundle, prefix):
    bundle.install(prefix)
    mine = prefix / "bin" / "helper"
    mine.write_text("mine", encoding="utf-8")
    (fake_bundle / "bin" / "helper").write_text("new", encoding="utf-8")
    with pytest.raises(bundle.FastNvimError):
        bundle.install(prefix)
    assert mine.read_text(encoding="utf-8") == "mine"


def test_bin_names_are_compared_the_way_the_file_system_does(fake_bundle, prefix, monkeypatch):
    """Windows: 0.10 ships dbghelp.dll, 0.11 DbgHelp.dll -- the same file there."""
    bundle.install(prefix)
    marker = prefix / bundle.MARKER
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["bin"].append("dbghelp.dll")
    marker.write_text(json.dumps(data), encoding="utf-8")
    (prefix / "bin" / "DbgHelp.dll").write_text("ours, other spelling", encoding="utf-8")
    (fake_bundle / "bin" / "DbgHelp.dll").write_text("new", encoding="utf-8")
    monkeypatch.setattr(bundle.os.path, "normcase", str.lower)  # as on Windows
    bundle.install(prefix)
    assert (prefix / "bin" / "DbgHelp.dll").read_text(encoding="utf-8") == "new"


def test_an_interrupted_install_can_be_rerun_without_force(fake_bundle, host, prefix, monkeypatch):
    real_copy = bundle._copy

    def copy_then_fail(source, dest):
        real_copy(source, dest)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy", copy_then_fail)
    with pytest.raises(OSError):
        bundle.install(prefix)
    monkeypatch.setattr(bundle, "_copy", real_copy)  # not undo(): keep fake_bundle
    bundle.install(prefix)
    assert (prefix / "bin" / host.exe).is_file()


@pytest.mark.parametrize("old_name", ["0.10-{key}.json", "install.json"])
def test_prefixes_from_older_versions_are_recognised(fake_bundle, host, prefix, old_name):
    """0.2.x wrote <release>-<platform>.json; 0.3.x install.json with a release field."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / host.exe).write_text("old nvim", encoding="utf-8")
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    old = prefix / bundle.MARKER_DIR / old_name.format(key=host.key)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"release": "0.10", "bin": [host.exe]}), encoding="utf-8")
    bundle.install(prefix)  # no --force needed
    assert [p.name for p in (prefix / bundle.MARKER_DIR).glob("*.json")] == ["install.json"]
    assert bundle.installed(prefix)["line"] == "0.12"


@pytest.mark.parametrize("names", ["nvim", ["../../escape", "/abs", "a\\b", ".."]])
def test_marker_bin_names_cannot_reach_outside_bin(fake_bundle, prefix, names):
    victim = prefix / "escape"
    victim.parent.mkdir(parents=True)
    victim.write_text("keep", encoding="utf-8")
    marker = prefix / bundle.MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"line": "0.12", "bin": names}), encoding="utf-8")
    bundle.install(prefix)
    assert victim.read_text(encoding="utf-8") == "keep"


@POSIX_ONLY
def test_verify_accepts_a_healthy_install(fake_bundle, prefix):
    bundle.verify(bundle.install(prefix), prefix)


@POSIX_ONLY
def test_verify_notices_a_missing_runtime(fake_bundle, prefix):
    exe = bundle.install(prefix)
    bundle._remove(prefix / "share" / "nvim" / "runtime")
    with pytest.raises(bundle.FastNvimError, match="did not start with the runtime"):
        bundle.verify(exe, prefix)


@POSIX_ONLY
def test_verify_ignores_the_users_vimruntime(host, tmp_path, monkeypatch):
    tree = make_bundle(tmp_path / "picky", host)
    exe = tree / "bin" / host.exe
    # a binary that, like real Neovim, prefers $VIMRUNTIME when it is set
    exe.write_text(
        '#!/bin/sh\nprintf "%s" "${VIMRUNTIME:-$(dirname "$0")/../share/nvim/runtime}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VIMRUNTIME", "/somewhere/else")
    bundle.verify(exe, tree)


def test_default_prefix_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert bundle.default_prefix().is_absolute()
