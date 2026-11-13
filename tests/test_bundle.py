"""Installing, linking and unpacking the vendored Neovim trees."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from fast_nvim_latest import __version__, bundle
from fast_nvim_latest.platforms import Target, host_target

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions/symlinks")


def _target(**overrides) -> Target:
    base = dict(
        key="linux-x86_64",
        family="linux",
        arch="x86_64",
        asset="fake.tar.gz",
        archive="tar.gz",
        strip_prefix="fake-root",
        sha256="",
        size=0,
        exe="nvim",
        wheel_tag="manylinux_2_34_x86_64",
    )
    base.update(overrides)
    return Target(**base)


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
            "fake-root/bin/nvim": (b"#!/bin/sh\n", 0o755),
            "fake-root/lib/nvim/parser/c.so": (b"so", 0o644),
            "fake-root/share/nvim/runtime/syntax/syntax.vim": (b"vim", 0o644),
            "fake-root/share/icons/nvim.png": (b"png", 0o644),
            "fake-root/share/man/man1/nvim.1": (b"man", 0o644),
            "fake-root/README.md": (b"readme", 0o644),
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
            "fake-win/bin/nvim.exe": (b"MZ", 0o755),
            "fake-win/bin/lua51.dll": (b"MZ", 0o644),
            "fake-win/share/nvim/runtime/init.lua": (b"-- lua", 0o644),
        },
    )
    target = _target(
        key="windows-x86_64",
        family="windows",
        archive="zip",
        strip_prefix="fake-win",
        exe="nvim.exe",
    )
    dest = bundle.extract_archive(target, archive, tmp_path / "out")
    assert (dest / "bin" / "nvim.exe").is_file()
    assert (dest / "bin" / "lua51.dll").is_file()
    assert (dest / "share" / "nvim" / "runtime" / "init.lua").is_file()


def test_extract_rejects_path_traversal(tmp_path):
    archive = _tar(
        tmp_path / "evil.tar.gz",
        {
            "fake-root/bin/nvim": (b"x", 0o755),
            "fake-root/../../evil.txt": (b"x", 0o644),
        },
    )
    dest = bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
    assert dest.parent == tmp_path


def test_extract_without_binary_fails(tmp_path):
    archive = _tar(
        tmp_path / "nobin.tar.gz",
        {"fake-root/share/nvim/runtime/x.vim": (b"x", 0o644)},
    )
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert "bin/nvim" in str(excinfo.value)


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_install_copy(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)
    result = bundle.install(host, source, prefix)

    assert result["linked"] is False
    assert (prefix / "bin" / host.exe).is_file()
    assert os.access(prefix / "bin" / host.exe, os.X_OK)
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()
    assert (prefix / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert bundle.is_managed(prefix, host.key)
    assert bundle.installed_targets(prefix) == [host.key]
    assert bundle.neovim_version(prefix / "bin" / host.exe) == "NVIM v9.9.9"


@POSIX_ONLY
def test_install_is_idempotent(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)
    bundle.install(host, source, prefix)
    bundle.install(host, source, prefix)
    assert bundle.neovim_version(prefix / "bin" / host.exe) == "NVIM v9.9.9"


@POSIX_ONLY
def test_link_install_and_switch_back_to_copy(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)

    bundle.install(host, source, prefix, link=True)
    assert (prefix / "bin" / host.exe).is_symlink()
    assert (prefix / "share" / "nvim" / "runtime").is_symlink()
    assert bundle.neovim_version(prefix / "bin" / host.exe) == "NVIM v9.9.9"

    # linking again is a no-op
    again = bundle.install(host, source, prefix, link=True)
    assert again["files"] == 0

    bundle.install(host, source, prefix, link=False)
    assert not (prefix / "bin" / host.exe).is_symlink()
    assert (prefix / "share" / "nvim" / "runtime").is_dir()
    assert not (prefix / "share" / "nvim" / "runtime").is_symlink()
    assert bundle.neovim_version(prefix / "bin" / host.exe) == "NVIM v9.9.9"


@POSIX_ONLY
def test_install_restores_missing_exec_bit(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)
    bundle.install(host, source, prefix)
    exe = prefix / "bin" / host.exe
    exe.chmod(0o644)
    assert not os.access(exe, os.X_OK)
    bundle.ensure_executable(exe)
    assert os.access(exe, os.X_OK)


def test_install_keeps_other_files_in_prefix_bin(fake_bundle, prefix):
    host = host_target()
    (prefix / "bin").mkdir(parents=True)
    unrelated = prefix / "bin" / "other-tool"
    unrelated.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    assert unrelated.is_file()
    assert (prefix / "bin" / host.exe).is_file()


def test_install_records_a_complete_marker(fake_bundle, prefix):
    host = host_target()
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    info = bundle.marker_info(prefix, host.key)
    assert info["complete"] is True
    assert info["entries"] == 4
    assert info["bin"] == [host.exe]
    assert bundle.is_complete(prefix, host.key)


def test_marker_records_provenance(fake_bundle, prefix):
    host = host_target()
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    import json

    marker = json.loads(bundle.marker_file(prefix, host.key).read_text(encoding="utf-8"))
    assert marker["target"] == host.key
    assert marker["nvim_version"]
    assert marker["link"] is False


def test_bundled_targets_are_detected(fake_bundle):
    host = host_target()
    assert bundle.vendored_keys() == [host.key]
    assert bundle.has_bundled(host)
    assert bundle.bundled_nvim_version() == "v9.9.9"


def test_default_prefix_can_be_overridden(monkeypatch):
    monkeypatch.setenv("FAST_NVIM_LATEST_PREFIX", "/tmp/custom-nvim-prefix")
    assert str(bundle.default_prefix()) == "/tmp/custom-nvim-prefix"


def test_human_size():
    assert bundle.human_size(999) == "999 B"
    assert bundle.human_size(1024) == "1.0 KiB"
    assert bundle.human_size(34 * 1024 * 1024).endswith("MiB")


def test_bundle_parts_cover_binary_and_runtime():
    parts = {part for part, _ in bundle.BUNDLE_PARTS}
    assert parts == {"bin", "lib/nvim", "share/nvim/runtime"}


# --------------------------------------------------------------------------
# re-installs must not leave two Neovim versions behind
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_reinstall_removes_stale_files_from_managed_prefix(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)
    bundle.install(host, source, prefix)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text('" from an older Neovim\n', encoding="utf-8")
    old_parser = prefix / "lib" / "nvim" / "parser" / "removed.so"
    old_parser.write_bytes(b"old")
    bundle.install(host, source, prefix)
    assert not stale.exists()
    assert not old_parser.exists()
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


@POSIX_ONLY
def test_reinstall_removes_old_bin_entries_but_keeps_foreign_ones(fake_bundle, prefix):
    host = host_target()
    source = bundle.bundle_path(host.key)
    bundle.install(host, source, prefix)
    foreign = prefix / "bin" / "other-tool"
    foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    # a DLL the previous version shipped and this one does not
    stale = prefix / "bin" / "old.dll"
    stale.write_bytes(b"MZ")
    marker = bundle.marker_info(prefix, host.key)
    marker["bin"] = [host.exe, "old.dll"]
    bundle.marker_file(prefix, host.key).write_text(json.dumps(marker), encoding="utf-8")

    bundle.install(host, source, prefix)
    assert not stale.exists()
    assert foreign.is_file()


@POSIX_ONLY
def test_replace_flag_clears_a_foreign_prefix(fake_bundle, prefix):
    host = host_target()
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text("stale", encoding="utf-8")
    bundle.install(host, bundle.bundle_path(host.key), prefix, replace=True)
    assert not stale.exists()
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


# --------------------------------------------------------------------------
# failures must not wedge the prefix
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_failed_install_leaves_a_recoverable_prefix(fake_bundle, prefix, monkeypatch):
    host = host_target()
    source = bundle.bundle_path(host.key)

    def explode(source, dest):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(bundle, "_copy_tree", explode)
    with pytest.raises(OSError):
        bundle.install(host, source, prefix)

    # the prefix is recognised as ours, so a retry needs no --force
    assert bundle.is_managed(prefix, host.key)
    assert bundle.is_complete(prefix, host.key) is False
    assert not (prefix / bundle.MARKER_DIR / ".lock").exists()

    monkeypatch.undo()
    bundle.install(host, source, prefix)
    assert bundle.is_complete(prefix, host.key)
    assert bundle.neovim_version(prefix / "bin" / host.exe) == "NVIM v9.9.9"


@POSIX_ONLY
def test_a_lock_left_by_a_dead_process_is_ignored(fake_bundle, prefix):
    host = host_target()
    lock = prefix / bundle.MARKER_DIR / ".lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("999999999\n", encoding="ascii")  # no such process
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    assert not lock.exists()
    assert bundle.is_complete(prefix, host.key)


# --------------------------------------------------------------------------
# the post-install check has to look at the runtime, not just --version
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_verify_install_accepts_a_healthy_prefix(fake_bundle, prefix):
    host = host_target()
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    assert bundle.verify_install(prefix / "bin" / host.exe, prefix) == "NVIM v9.9.9"


@POSIX_ONLY
def test_verify_install_notices_a_missing_runtime(fake_bundle, prefix):
    host = host_target()
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    shutil.rmtree(prefix / "share" / "nvim" / "runtime")
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.verify_install(prefix / "bin" / host.exe, prefix)
    assert "VIMRUNTIME" in str(excinfo.value)


# --------------------------------------------------------------------------
# --link reporting
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_link_install_reports_links_and_no_copied_bytes(fake_bundle, prefix):
    host = host_target()
    result = bundle.install(host, bundle.bundle_path(host.key), prefix, link=True)
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
        info = tarfile.TarInfo("fake-root/bin/nvim")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("fake-root/share/nvim/runtime/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../../../tmp/evil"
        tar.addfile(link)
        nested = tarfile.TarInfo("fake-root/share/nvim/runtime/escape/owned")
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
            ("fake-root/bin/nvim", b"#!/bin/sh\n"),
            ("fake-root/share/nvim/runtime/lua", b"-- lua\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("fake-root/share/nvim/runtime/alias")
        link.type = tarfile.SYMTYPE
        link.linkname = "lua"
        tar.addfile(link)

    dest = bundle.extract_archive(_target(), archive, tmp_path / "out")
    assert (dest / "share" / "nvim" / "runtime" / "alias").is_symlink()


def test_a_broken_archive_does_not_destroy_the_cached_tree(tmp_path):
    good = _tar(tmp_path / "good.tar.gz", {"fake-root/bin/nvim": (b"#!/bin/sh\n", 0o755)})
    dest = bundle.extract_archive(_target(), good, tmp_path / "out")
    broken = _tar(tmp_path / "broken.tar.gz", {"fake-root/README.md": (b"x", 0o644)})
    with pytest.raises(bundle.FastNvimError):
        bundle.extract_archive(_target(), broken, dest)
    assert (dest / "bin" / "nvim").is_file()
    assert not list(tmp_path.glob(".extract-*"))


def test_windows_zip_with_a_symlink_is_refused(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("fake-win/bin/nvim.exe")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(info, b"MZ")
        link = zipfile.ZipInfo("fake-win/share/nvim/runtime/alias")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "nvim.exe")
    target = _target(
        key="windows-x86_64",
        family="windows",
        archive="zip",
        strip_prefix="fake-win",
        exe="nvim.exe",
    )
    with pytest.raises(bundle.FastNvimError) as excinfo:
        bundle.extract_archive(target, archive, tmp_path / "out")
    assert "symlink" in str(excinfo.value)


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
def test_user_agent_has_the_real_version():
    assert "{}" not in bundle.USER_AGENT
    assert __version__ in bundle.USER_AGENT


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
