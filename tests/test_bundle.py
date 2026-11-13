"""Installing, linking and unpacking the vendored Neovim trees."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from fast_nvim_release import __version__, bundle
from fast_nvim_release.platforms import (
    RELEASE_KEYS,
    RELEASES,
    Target,
)

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions/symlinks")


def _target(release_key: str = "0.12", platform_key: str = "linux-x86_64", **overrides) -> Target:
    base = RELEASES[release_key].target(platform_key)
    assert base is not None
    if not overrides:
        return base
    values = {
        "release": base.release,
        "platform": base.platform,
        "sha256": "",
        "size": 0,
    }
    values.update(overrides)
    return Target(**values)


def _tar(path: Path, members: dict) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, (data, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))
    return path


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, (data, mode) in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (mode | stat.S_IFREG) << 16
            zf.writestr(info, data)
    return path


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
def test_extract_tar_keeps_only_runtime_parts(tmp_path):
    archive = _tar(
        tmp_path / "fake.tar.gz",
        {
            "nvim-linux-x86_64/bin/nvim": (b"#!/bin/sh\n", 0o755),
            "nvim-linux-x86_64/lib/nvim/parser/c.so": (b"so", 0o644),
            "nvim-linux-x86_64/share/nvim/runtime/syntax/syntax.vim": (b"vim", 0o644),
            "nvim-linux-x86_64/share/icons/nvim.png": (b"png", 0o644),
            "nvim-linux-x86_64/share/man/man1/nvim.1": (b"man", 0o644),
            "nvim-linux-x86_64/README.md": (b"readme", 0o644),
        },
    )
    dest = bundle.extract_archive(_target(), archive, tmp_path / "out")
    kept = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert kept == [
        "bin/nvim",
        "lib/nvim/parser/c.so",
        "share/nvim/runtime/syntax/syntax.vim",
    ]
    assert os.access(dest / "bin" / "nvim", os.X_OK)


def test_extract_zip_for_windows_target(tmp_path):
    archive = _zip(
        tmp_path / "fake.zip",
        {
            "nvim-win64/bin/nvim.exe": (b"MZ", 0o755),
            "nvim-win64/bin/lua51.dll": (b"MZ", 0o644),
            "nvim-win64/share/nvim/runtime/init.lua": (b"-- lua", 0o644),
        },
    )
    target = _target("0.12", "windows-x86_64")
    dest = bundle.extract_archive(target, archive, tmp_path / "out")
    assert (dest / "bin" / "nvim.exe").is_file()
    assert (dest / "bin" / "lua51.dll").is_file()
    assert (dest / "share" / "nvim" / "runtime" / "init.lua").is_file()


def test_extract_rejects_path_traversal(tmp_path):
    archive = _tar(
        tmp_path / "evil.tar.gz",
        {
            "nvim-linux-x86_64/bin/nvim": (b"x", 0o755),
            "nvim-linux-x86_64/../../evil.txt": (b"x", 0o644),
        },
    )
    dest = bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
    assert dest.parent == tmp_path


def test_extract_without_binary_fails(tmp_path):
    archive = _tar(
        tmp_path / "nobin.tar.gz",
        {"nvim-linux-x86_64/share/nvim/runtime/x.vim": (b"x", 0o644)},
    )
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert "bin/nvim" in str(excinfo.value)


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_install_copy(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    result = bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)

    assert result["linked"] is False
    assert (prefix / "bin" / target.exe).is_file()
    assert os.access(prefix / "bin" / target.exe, os.X_OK)
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()
    assert (prefix / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert bundle.is_managed(prefix, "0.12", host_key)
    assert bundle.installed_releases(prefix) == ["0.12"]
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"


@POSIX_ONLY
def test_install_is_idempotent(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)
    bundle.install(target, source, prefix)
    bundle.install(target, source, prefix)
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"


@POSIX_ONLY
def test_installing_another_release_replaces_the_previous_one(fake_bundle, prefix, host_key):
    """A prefix holds one Neovim, so switching releases must not merge trees."""
    if RELEASES["0.10"].target(host_key) is None:  # pragma: no cover
        pytest.skip("no 0.10 build for this platform")
    exe = RELEASES["0.10"].target(host_key).exe
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "only-in-0.12.vim"
    stale.write_text('" old\n', encoding="utf-8")

    bundle.install(RELEASES["0.10"].target(host_key), bundle.bundle_path("0.10", host_key), prefix)
    assert not stale.exists()
    assert bundle.installed_releases(prefix) == ["0.10"]
    assert bundle.neovim_version(prefix / "bin" / exe) == "NVIM 0.10.4"
    # the marker for the replaced release is gone, so `path` cannot lie
    assert not bundle.is_managed(prefix, "0.12", host_key)


@POSIX_ONLY
def test_link_install_and_switch_back_to_copy(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)

    bundle.install(target, source, prefix, link=True)
    assert (prefix / "bin" / target.exe).is_symlink()
    assert (prefix / "share" / "nvim" / "runtime").is_symlink()
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"

    # linking again is a no-op
    again = bundle.install(target, source, prefix, link=True)
    assert again["files"] == 0

    bundle.install(target, source, prefix, link=False)
    assert not (prefix / "bin" / target.exe).is_symlink()
    assert (prefix / "share" / "nvim" / "runtime").is_dir()
    assert not (prefix / "share" / "nvim" / "runtime").is_symlink()
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"


@POSIX_ONLY
def test_install_restores_missing_exec_bit(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    exe = prefix / "bin" / target.exe
    exe.chmod(0o644)
    assert not os.access(exe, os.X_OK)
    bundle.ensure_executable(exe)
    assert os.access(exe, os.X_OK)


def test_install_keeps_other_files_in_prefix_bin(fake_bundle, prefix, host_key):
    (prefix / "bin").mkdir(parents=True)
    unrelated = prefix / "bin" / "other-tool"
    unrelated.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    assert unrelated.is_file()
    assert (prefix / "bin" / RELEASES["0.12"].target(host_key).exe).is_file()


def test_install_records_a_complete_marker(fake_bundle, prefix, host_key):
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    info = bundle.marker_info(prefix, "0.12", host_key)
    assert info["complete"] is True
    assert info["entries"] == 4
    assert info["release"] == "0.12"
    assert info["nvim_version"] == "v0.12.6"
    assert bundle.is_complete(prefix, "0.12", host_key)


def test_marker_records_provenance(fake_bundle, prefix, host_key):
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    marker = json.loads(bundle.marker_file(prefix, "0.12", host_key).read_text(encoding="utf-8"))
    assert marker["target"] == host_key
    assert marker["release"] == "0.12"
    assert marker["nvim_version"]
    assert marker["link"] is False


def test_bundled_targets_are_detected(fake_bundle, host_key):
    assert bundle.vendored_releases() == list(RELEASE_KEYS)
    assert [t.label for t in bundle.vendored_targets()] == [
        f"{key}/{host_key}" for key in RELEASE_KEYS
    ]
    target = RELEASES["0.11"].target(host_key)
    assert bundle.has_bundled(target)
    assert bundle.bundled_nvim_version("0.11") == "v0.11.7"


def test_download_only_builds_are_never_bundled(tmp_path, monkeypatch, host_key):
    """0.10 on linux-arm64 exists but must not count as bundled."""
    root = tmp_path / "_nvim"
    root.mkdir()
    (root / "0.10" / "linux-arm64" / "bin").mkdir(parents=True)
    (root / "0.10" / "linux-arm64" / "bin" / "nvim").write_text("x", encoding="utf-8")
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    assert bundle.vendored_platforms("0.10") == []
    assert bundle.has_bundled(RELEASES["0.10"].target("linux-arm64")) is False


def test_default_prefix_can_be_overridden(monkeypatch, tmp_path):
    wanted = tmp_path / "custom-nvim-prefix"
    monkeypatch.setenv("FAST_NVIM_RELEASE_PREFIX", str(wanted))
    assert bundle.default_prefix() == wanted


def test_the_pre_rename_env_names_still_work(monkeypatch, tmp_path):
    """Anyone with the old variables exported keeps working after the rename."""
    wanted = tmp_path / "old-prefix"
    monkeypatch.delenv("FAST_NVIM_RELEASE_PREFIX", raising=False)
    monkeypatch.setenv("FAST_NVIM_LATEST_PREFIX", str(wanted))
    assert bundle.default_prefix() == wanted

    cache = tmp_path / "old-cache"
    monkeypatch.delenv("FAST_NVIM_RELEASE_CACHE_DIR", raising=False)
    monkeypatch.setenv("FAST_NVIM_LATEST_CACHE_DIR", str(cache))
    assert bundle.cache_root() == cache


def test_relative_paths_are_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert bundle.normalize("some/prefix") == tmp_path / "some" / "prefix"
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", "cache")
    assert bundle.cache_root() == tmp_path / "cache"
    assert bundle.cache_root().is_absolute()


def test_human_size():
    assert bundle.human_size(999) == "999 B"
    assert bundle.human_size(1024) == "1.0 KiB"
    assert bundle.human_size(34 * 1024 * 1024).endswith("MiB")


def test_bundle_parts_cover_binary_and_runtime():
    parts = {part for part, _ in bundle.BUNDLE_PARTS}
    assert parts == {"bin", "lib/nvim", "share/nvim/runtime"}


def test_cache_paths_are_per_release(fake_bundle, host_key):
    """Two releases must never share a cache directory."""
    seen = set()
    for release_key in RELEASE_KEYS:
        target = RELEASES[release_key].target(host_key)
        seen.add(bundle.cached_archive(target))
        seen.add(bundle.cached_tree(target))
    assert len(seen) == 2 * len(RELEASE_KEYS)
    # the tree is namespaced by release and platform
    target = RELEASES["0.10"].target(host_key)
    assert bundle.cached_tree(target).parts[-2:] == ("0.10", host_key)


# --------------------------------------------------------------------------
# re-installs must not leave two Neovim versions behind
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_reinstall_removes_stale_files_from_managed_prefix(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)
    bundle.install(target, source, prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text('" from an older Neovim\n', encoding="utf-8")
    old_parser = prefix / "lib" / "nvim" / "parser" / "removed.so"
    old_parser.write_bytes(b"old")
    bundle.install(target, source, prefix)
    assert not stale.exists()
    assert not old_parser.exists()
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


@POSIX_ONLY
def test_reinstall_removes_old_bin_entries_but_keeps_foreign_ones(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)
    bundle.install(target, source, prefix)
    foreign = prefix / "bin" / "other-tool"
    foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    # a DLL the previous version shipped and this one does not
    stale = prefix / "bin" / "old.dll"
    stale.write_bytes(b"MZ")
    marker = bundle.marker_info(prefix, "0.12", host_key)
    marker["bin"] = [target.exe, "old.dll"]
    bundle.marker_file(prefix, "0.12", host_key).write_text(json.dumps(marker), encoding="utf-8")

    bundle.install(target, source, prefix)
    assert not stale.exists()
    assert foreign.is_file()


@POSIX_ONLY
def test_replace_flag_clears_a_foreign_prefix(fake_bundle, prefix, host_key):
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text("stale", encoding="utf-8")
    bundle.install(
        RELEASES["0.12"].target(host_key),
        bundle.bundle_path("0.12", host_key),
        prefix,
        replace=True,
    )
    assert not stale.exists()
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


def test_marker_names_cannot_escape_the_prefix(fake_bundle, prefix, host_key):
    """A tampered marker must not be able to unlink files outside bin/."""
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    outside = prefix.parent / "victim.txt"
    outside.write_text("keep me", encoding="utf-8")
    marker = bundle.marker_info(prefix, "0.12", host_key)
    marker["bin"] = [target.exe, "../../victim.txt"]
    bundle.marker_file(prefix, "0.12", host_key).write_text(json.dumps(marker), encoding="utf-8")
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    assert outside.is_file()


# --------------------------------------------------------------------------
# failures must not wedge the prefix
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_failed_install_leaves_a_recoverable_prefix(fake_bundle, prefix, host_key, monkeypatch):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)

    def explode(source, dest):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy_tree", explode)
    with pytest.raises(OSError):
        bundle.install(target, source, prefix)

    # the prefix is recognised as ours, so a retry needs no --force
    assert bundle.is_managed(prefix, "0.12", host_key)
    assert bundle.is_complete(prefix, "0.12", host_key) is False
    assert not (prefix / bundle.MARKER_DIR / ".lock").exists()

    monkeypatch.undo()
    bundle.install(target, source, prefix)
    assert bundle.is_complete(prefix, "0.12", host_key)
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"


@POSIX_ONLY
def test_a_failed_copy_never_destroys_the_working_install(
    fake_bundle, prefix, host_key, monkeypatch
):
    """A full disk half way through an upgrade must leave the old build usable."""
    newest, oldest = RELEASE_KEYS[-1], RELEASE_KEYS[0]
    keep = RELEASES[newest].target(host_key)
    bundle.install(keep, bundle.bundle_path(newest, host_key), prefix)
    runtime = prefix / "share" / "nvim" / "runtime" / "init.lua"
    before = runtime.read_bytes()

    def explode(source, dest):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy_tree", explode)
    with pytest.raises(OSError):
        bundle.install(
            RELEASES[oldest].target(host_key), bundle.bundle_path(oldest, host_key), prefix
        )

    # the previous release is still complete, still linked to its own marker,
    # and the half-staged tree is gone
    assert runtime.read_bytes() == before
    assert bundle.is_complete(prefix, newest, host_key)
    assert bundle.is_complete(prefix, oldest, host_key) is False
    assert not (prefix / bundle.MARKER_DIR / ".staging").exists()
    assert (
        bundle.neovim_version(prefix / "bin" / keep.exe) == f"NVIM {RELEASES[newest].version[1:]}"
    )


@POSIX_ONLY
def test_installed_state_needs_a_finished_install_not_just_a_marker(fake_bundle, prefix, host_key):
    """A marker left by a killed install must not count as "installed"."""
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    assert bundle.installed_releases(prefix) == ["0.12"]

    (prefix / "bin" / target.exe).unlink()  # as if the copy died half way
    assert bundle.is_managed(prefix, "0.12", host_key)  # the marker is still there
    assert bundle.installed_targets(prefix) == []
    assert bundle.installed_releases(prefix) == []


def test_a_leftover_staging_tree_is_cleared(fake_bundle, prefix, host_key):
    staging = prefix / bundle.MARKER_DIR / ".staging"
    (staging / "lib" / "nvim").mkdir(parents=True)
    (staging / "lib" / "nvim" / "stale.so").write_bytes(b"old")
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    assert not staging.exists()
    assert not (prefix / "lib" / "nvim" / "stale.so").exists()
    assert bundle.is_complete(prefix, "0.12", host_key)


@POSIX_ONLY
def test_staging_is_not_left_behind_by_a_successful_install(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    assert not (prefix / bundle.MARKER_DIR / ".staging").exists()
    assert bundle.neovim_version(prefix / "bin" / target.exe) == "NVIM 0.12.6"


@POSIX_ONLY
def test_a_lock_left_by_a_dead_process_is_ignored(fake_bundle, prefix, host_key):
    lock = prefix / bundle.MARKER_DIR / ".lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("999999999\n", encoding="ascii")  # no such process
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    assert not lock.exists()
    assert bundle.is_complete(prefix, "0.12", host_key)


def test_a_torn_marker_is_not_a_finished_install(fake_bundle, prefix, host_key):
    """A marker written by a killed install must not count as complete."""
    bundle.install(RELEASES["0.12"].target(host_key), bundle.bundle_path("0.12", host_key), prefix)
    bundle.marker_file(prefix, "0.12", host_key).write_text("", encoding="utf-8")
    assert bundle.is_managed(prefix, "0.12", host_key)
    assert bundle.is_complete(prefix, "0.12", host_key) is False


@POSIX_ONLY
def test_reinstall_after_a_torn_marker_still_replaces_stale_files(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    source = bundle.bundle_path("0.12", host_key)
    bundle.install(target, source, prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text('" from an older Neovim\n', encoding="utf-8")
    bundle.marker_file(prefix, "0.12", host_key).write_text("{}", encoding="utf-8")
    bundle.install(target, source, prefix)
    assert not stale.exists()
    assert bundle.is_complete(prefix, "0.12", host_key)


# --------------------------------------------------------------------------
# the post-install check has to look at the runtime, not just --version
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_verify_install_accepts_a_healthy_prefix(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    assert bundle.verify_install(prefix / "bin" / target.exe, prefix) == "NVIM 0.12.6"


@POSIX_ONLY
def test_verify_install_notices_a_missing_runtime(fake_bundle, prefix, host_key):
    target = RELEASES["0.12"].target(host_key)
    bundle.install(target, bundle.bundle_path("0.12", host_key), prefix)
    shutil.rmtree(prefix / "share" / "nvim" / "runtime")
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.verify_install(prefix / "bin" / target.exe, prefix)
    assert "VIMRUNTIME" in str(excinfo.value)


# --------------------------------------------------------------------------
# --link reporting
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_link_install_reports_links_and_no_copied_bytes(fake_bundle, prefix, host_key):
    result = bundle.install(
        RELEASES["0.12"].target(host_key),
        bundle.bundle_path("0.12", host_key),
        prefix,
        link=True,
    )
    assert result["files"] == 0
    assert result["links"] == 3
    assert result["bytes"] == 0


# --------------------------------------------------------------------------
# archives
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_extract_rejects_a_symlink_escaping_the_tree(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("nvim-linux-x86_64/bin/nvim")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("nvim-linux-x86_64/share/nvim/runtime/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../../tmp/evil"
        tar.addfile(link)
        nested = tarfile.TarInfo("nvim-linux-x86_64/share/nvim/runtime/escape/owned")
        nested.size = 1
        tar.addfile(nested, io.BytesIO(b"x"))

    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert "unsafe symlink" in str(excinfo.value)
    assert not (tmp_path / "out").exists()


@POSIX_ONLY
def test_extract_accepts_a_symlink_inside_the_tree(tmp_path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in (
            ("nvim-linux-x86_64/bin/nvim", b"#!/bin/sh\n"),
            ("nvim-linux-x86_64/share/nvim/runtime/lua", b"-- lua\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("nvim-linux-x86_64/share/nvim/runtime/alias")
        link.type = tarfile.SYMTYPE
        link.linkname = "lua"
        tar.addfile(link)

    dest = bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert (dest / "share" / "nvim" / "runtime" / "alias").is_symlink()


def test_stale_backups_are_cleaned_where_they_actually_are(tmp_path):
    """Trees live at <root>/<release>/<platform>, so backups sit one level down."""
    root = tmp_path / "_nvim"
    (root / "0.10" / "linux-x86_64.old" / "bin").mkdir(parents=True)
    (root / "0.10" / "linux-x86_64" / "bin").mkdir(parents=True)
    (root / "old-layout.old").mkdir()
    bundle.remove_stale_backups(root)
    assert not (root / "0.10" / "linux-x86_64.old").exists()
    assert not (root / "old-layout.old").exists()
    assert (root / "0.10" / "linux-x86_64" / "bin").is_dir()


# --------------------------------------------------------------------------
# two processes must not unpack into one cache entry at the same time
# --------------------------------------------------------------------------
def _cache_with_archive(tmp_path, monkeypatch) -> Target:
    """Put a real, correctly-pinned archive where ``ensure_bundle`` looks."""
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bundle, "has_bundled", lambda candidate: False)
    target = _target()
    archive = bundle.cached_archive(target)
    archive.parent.mkdir(parents=True, exist_ok=True)
    _tar(archive, {"nvim-linux-x86_64/bin/nvim": (b"#!/bin/sh\n", 0o755)})
    return _target(
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        size=archive.stat().st_size,
    )


def test_extraction_into_the_cache_holds_a_lock(tmp_path, monkeypatch):
    target = _cache_with_archive(tmp_path, monkeypatch)
    seen = {}
    real = bundle._lock

    def spy(path, timeout=bundle.LOCK_TIMEOUT):
        seen["path"] = path
        return real(path, timeout)

    monkeypatch.setattr(bundle, "_lock", spy)
    tree, origin = bundle.ensure_bundle(target, offline=True)
    assert tree == bundle.cached_tree(target)
    assert origin == "cache"
    assert seen["path"] == bundle.cache_lock(tree)
    assert (tree / "bin" / "nvim").is_file()
    assert not bundle.cache_lock(tree).exists()  # released again


@POSIX_ONLY
def test_two_callers_can_share_one_cache_entry(tmp_path, monkeypatch):
    """Both callers get a usable tree; the lock keeps them from colliding."""
    import threading
    import time

    target = _cache_with_archive(tmp_path, monkeypatch)
    real = bundle.extract_archive

    def slow(*args, **kwargs):
        time.sleep(0.2)  # widen the window the lock has to close
        return real(*args, **kwargs)

    monkeypatch.setattr(bundle, "extract_archive", slow)
    results, errors = [], []

    def work():
        try:
            results.append(bundle.ensure_bundle(target, offline=True)[0])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert results == [bundle.cached_tree(target)] * 2
    assert (bundle.cached_tree(target) / "bin" / "nvim").is_file()
    assert not list(bundle.cached_tree(target).parent.glob("*.old"))


def test_a_broken_archive_does_not_destroy_the_cached_tree(tmp_path):
    good = _tar(tmp_path / "good.tar.gz", {"nvim-linux-x86_64/bin/nvim": (b"#!/bin/sh\n", 0o755)})
    dest = bundle.extract_archive(_target(), good, tmp_path / "out")
    broken = _tar(tmp_path / "broken.tar.gz", {"nvim-linux-x86_64/README.md": (b"x", 0o644)})
    with pytest.raises(bundle.FastNvimError):
        bundle.extract_archive(_target(), broken, dest)
    assert (dest / "bin" / "nvim").is_file()
    assert not list(tmp_path.glob(".extract-*"))


def test_a_swap_killed_half_way_is_recovered(tmp_path):
    """If the old tree was moved aside but the new one never landed, restore it."""
    good = _tar(tmp_path / "good.tar.gz", {"nvim-linux-x86_64/bin/nvim": (b"#!/bin/sh\n", 0o755)})
    dest = tmp_path / "out"
    bundle.extract_archive(_target(), good, dest)
    backup = tmp_path / "out.old"
    os.replace(dest, backup)  # what the killed process left behind
    assert not dest.exists()

    bundle.extract_archive(_target(), good, dest)
    assert (dest / "bin" / "nvim").is_file()
    assert not backup.exists()


def test_windows_zip_with_a_symlink_is_refused(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("nvim-win64/bin/nvim.exe")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(info, b"MZ")
        link = zipfile.ZipInfo("nvim-win64/share/nvim/runtime/alias")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "nvim.exe")
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.extract_archive(_target("0.12", "windows-x86_64"), archive, tmp_path / "out")
    assert "symlink" in str(excinfo.value)


# --------------------------------------------------------------------------
# the start-up probe must not touch the user's state
# --------------------------------------------------------------------------
def test_the_runtime_probe_turns_user_config_and_shada_off():
    """``-u NONE`` alone still reads *and rewrites* the user's shada file."""
    assert (
        "-u" in bundle.PROBE_ARGS and bundle.PROBE_ARGS[bundle.PROBE_ARGS.index("-u") + 1] == "NONE"
    )
    assert (
        "-i" in bundle.PROBE_ARGS and bundle.PROBE_ARGS[bundle.PROBE_ARGS.index("-i") + 1] == "NONE"
    )
    assert "+lua io.write(vim.env.VIMRUNTIME)" in bundle.PROBE_ARGS


@POSIX_ONLY
def test_runtime_path_uses_the_safe_probe(fake_bundle, host_key, monkeypatch):
    seen = {}
    real = bundle._run

    def spy(exe, args, timeout):
        seen["args"] = list(args)
        return real(exe, args, timeout)

    monkeypatch.setattr(bundle, "_run", spy)
    reported = bundle.runtime_path(bundle.bundle_exe("0.12", host_key))
    assert seen["args"] == list(bundle.PROBE_ARGS)
    assert reported.endswith("share/nvim/runtime")


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
def test_user_agent_has_the_real_version():
    assert "{}" not in bundle.USER_AGENT
    assert __version__ in bundle.USER_AGENT
    assert "fast-nvim-release" in bundle.USER_AGENT


def test_downloads_use_a_per_process_partial_file(monkeypatch, tmp_path):
    seen = {}

    class Response:
        def __init__(self):
            self.headers = {"Content-Length": "2"}
            self.rest = b"hi"

        def read(self, size):
            seen["partials"] = [p.name for p in tmp_path.iterdir() if ".part" in p.name]
            data, self.rest = self.rest, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        bundle.urllib.request,
        "urlopen",
        lambda request, timeout=None: Response(),
    )
    target = _target(sha256="")
    bundle._download(target, tmp_path / "archive.tar.gz")
    assert seen["partials"] == [f"archive.tar.gz.part-{os.getpid()}"]
    assert (tmp_path / "archive.tar.gz").read_bytes() == b"hi"
    assert not list(tmp_path.glob("*.part-*"))
