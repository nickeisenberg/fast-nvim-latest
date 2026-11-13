"""Installing, linking and unpacking the vendored Neovim trees."""

from __future__ import annotations

import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from fast_nvim import bundle
from fast_nvim.platforms import Target, host_target

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
    kept = sorted(
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
    )
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


def test_install_dry_run_writes_nothing(fake_bundle, prefix):
    host = host_target()
    result = bundle.install(host, bundle.bundle_path(host.key), prefix, dry_run=True)
    assert result["files"] == 0
    assert not prefix.exists()


def test_marker_records_provenance(fake_bundle, prefix):
    host = host_target()
    bundle.install(host, bundle.bundle_path(host.key), prefix)
    import json

    marker = json.loads(
        bundle.marker_file(prefix, host.key).read_text(encoding="utf-8")
    )
    assert marker["target"] == host.key
    assert marker["nvim_version"]
    assert marker["link"] is False


def test_bundled_targets_are_detected(fake_bundle):
    host = host_target()
    assert bundle.vendored_keys() == [host.key]
    assert bundle.has_bundled(host)
    assert bundle.bundled_nvim_version() == "v9.9.9"


def test_default_prefix_can_be_overridden(monkeypatch):
    monkeypatch.setenv("FAST_NVIM_PREFIX", "/tmp/custom-nvim-prefix")
    assert str(bundle.default_prefix()) == "/tmp/custom-nvim-prefix"


def test_human_size():
    assert bundle.human_size(999) == "999 B"
    assert bundle.human_size(1024) == "1.0 KiB"
    assert bundle.human_size(34 * 1024 * 1024).endswith("MiB")


def test_bundle_parts_cover_binary_and_runtime():
    parts = {part for part, _ in bundle.BUNDLE_PARTS}
    assert parts == {"bin", "lib/nvim", "share/nvim/runtime"}
