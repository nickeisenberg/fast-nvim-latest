"""Installing a bundled build into a prefix."""

from __future__ import annotations

import json
import os

import pytest
from conftest import POSIX_ONLY, make_bundle

from fast_nvim_release import bundle
from fast_nvim_release.platforms import RELEASES


def runtime_marker(prefix):
    return (prefix / "share" / "nvim" / "runtime" / "init.lua").read_text(encoding="utf-8")


def test_bundled_releases(fake_bundle, host):
    expected = [key for key, release in RELEASES.items() if host.key in release.sha256]
    assert bundle.bundled_releases(host) == expected


def test_a_missing_build_says_what_the_wheel_has(fake_bundle, host, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", empty)
    with pytest.raises(bundle.FastNvimError, match="it has: none"):
        bundle.bundled_exe("0.12", host)


def test_install_copies_the_tree_and_records_it(fake_bundle, host, prefix):
    exe = bundle.install("0.12", host, prefix)
    assert exe == prefix / "bin" / host.exe
    assert exe.is_file()
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
    assert (prefix / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert runtime_marker(prefix) == "-- 0.12\n"
    info = bundle.installed(prefix)
    assert info["release"] == "0.12"
    assert info["nvim_version"] == "v0.12.6"
    assert info["bin"] == [host.exe]


def test_switching_releases_replaces_the_build_and_keeps_unrelated_files(fake_bundle, host, prefix):
    bundle.install("0.12", host, prefix)
    (fake_bundle / "0.12" / host.key / "bin" / "helper").write_text("x", encoding="utf-8")
    bundle.install("0.12", host, prefix)  # now owns bin/helper too
    (prefix / "bin" / "other-tool").write_text("mine", encoding="utf-8")

    bundle.install("0.11", host, prefix)
    assert runtime_marker(prefix) == "-- 0.11\n"
    assert not (prefix / "bin" / "helper").exists()  # owned by the 0.12 build
    assert (prefix / "bin" / "other-tool").read_text(encoding="utf-8") == "mine"
    assert bundle.installed(prefix)["release"] == "0.11"


def test_a_stale_runtime_file_does_not_survive_a_reinstall(fake_bundle, host, prefix):
    bundle.install("0.12", host, prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text("old", encoding="utf-8")
    bundle.install("0.12", host, prefix)
    assert not stale.exists()


@pytest.mark.parametrize("existing", ["bin/{exe}", "bin/helper", "lib/nvim", "share/nvim/runtime"])
def test_files_this_tool_did_not_install_are_never_overwritten(fake_bundle, host, prefix, existing):
    (fake_bundle / "0.12" / host.key / "bin" / "helper").write_text("ours", encoding="utf-8")
    target = prefix / existing.format(exe=host.exe)
    target.parent.mkdir(parents=True)
    if existing.startswith("bin/"):
        target.write_text("mine", encoding="utf-8")
    else:
        target.mkdir()

    with pytest.raises(bundle.FastNvimError, match="--force") as excinfo:
        bundle.install("0.12", host, prefix)
    assert str(target) in str(excinfo.value)
    assert not bundle.installed(prefix)

    bundle.install("0.12", host, prefix, force=True)
    assert bundle.installed(prefix)["release"] == "0.12"


def test_a_managed_prefix_still_protects_bin_entries_it_never_owned(fake_bundle, host, prefix):
    bundle.install("0.12", host, prefix)
    mine = prefix / "bin" / "helper"
    mine.write_text("mine", encoding="utf-8")
    (fake_bundle / "0.12" / host.key / "bin" / "helper").write_text("new", encoding="utf-8")
    with pytest.raises(bundle.FastNvimError):
        bundle.install("0.12", host, prefix)
    assert mine.read_text(encoding="utf-8") == "mine"


def test_an_interrupted_install_can_be_rerun_without_force(fake_bundle, host, prefix, monkeypatch):
    real_copy = bundle._copy

    def copy_then_fail(source, dest):
        real_copy(source, dest)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy", copy_then_fail)
    with pytest.raises(OSError):
        bundle.install("0.12", host, prefix)
    # restore only _copy: undo() would also drop the fake_bundle fixture's patch
    monkeypatch.setattr(bundle, "_copy", real_copy)
    bundle.install("0.12", host, prefix)
    assert (prefix / "bin" / host.exe).is_file()


def test_a_prefix_from_an_older_version_is_recognised_and_migrated(fake_bundle, host, prefix):
    """0.2.x wrote share/fast-nvim-release/<release>-<platform>.json."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / host.exe).write_text("old nvim", encoding="utf-8")
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    old = prefix / bundle.MARKER_DIR / f"0.10-{host.key}.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"release": "0.10", "bin": [host.exe]}), encoding="utf-8")

    assert bundle.installed(prefix)["release"] == "0.10"
    bundle.install("0.12", host, prefix)
    assert not old.exists()
    assert bundle.installed(prefix)["release"] == "0.12"


@pytest.mark.parametrize("names", ["nvim", ["../../escape", "/abs", "a\\b", ".."]])
def test_marker_bin_names_cannot_reach_outside_bin(fake_bundle, host, prefix, names):
    victim = prefix / "escape"
    victim.parent.mkdir(parents=True)
    victim.write_text("keep", encoding="utf-8")
    marker = prefix / bundle.MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"release": "0.12", "bin": names}), encoding="utf-8")
    bundle.install("0.12", host, prefix)
    assert victim.read_text(encoding="utf-8") == "keep"


@POSIX_ONLY
def test_verify_accepts_a_healthy_install(fake_bundle, host, prefix):
    exe = bundle.install("0.12", host, prefix)
    bundle.verify(exe, prefix)


@POSIX_ONLY
def test_verify_notices_a_missing_runtime(fake_bundle, host, prefix):
    exe = bundle.install("0.12", host, prefix)
    bundle._remove(prefix / "share" / "nvim" / "runtime")
    with pytest.raises(bundle.FastNvimError, match="did not start with the runtime"):
        bundle.verify(exe, prefix)


@POSIX_ONLY
def test_verify_ignores_the_users_vimruntime(fake_bundle, host, prefix, tmp_path, monkeypatch):
    tree = make_bundle(tmp_path / "picky", "0.12", host)
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
