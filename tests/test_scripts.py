"""The build-time scripts: fetching, wheel validation, upstream check."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from dataclasses import replace

import check_upstream
import check_wheels
import fetch_nvim
import pytest

from fast_nvim_release.platforms import OLD_GLIBC_RELEASES, PLATFORMS, RELEASES, WHEELS

LINUX = PLATFORMS["linux-x86_64"]


# --------------------------------------------------------------------------
# fetch_nvim: download, verify, unpack
# --------------------------------------------------------------------------
def _tar(path, members, links=()):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


#: tarfile extraction filters: 3.12+, backported to 3.9.17+/3.10.12+/3.11.4+;
#: setup-python's 3.9 on macOS/Windows is 3.9.13, which has none
NEEDS_TAR_FILTERS = pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"), reason="this Python has no tarfile filters"
)

NVIM_TREE = {
    "nvim-linux-x86_64/bin/nvim": b"#!/bin/sh\n",
    "nvim-linux-x86_64/lib/nvim/parser/c.so": b"so",
    "nvim-linux-x86_64/share/nvim/runtime/init.lua": b"--",
    "nvim-linux-x86_64/share/man/man1/nvim.1": b"not needed at runtime",
}


@NEEDS_TAR_FILTERS
def test_unpack_keeps_only_what_neovim_needs(tmp_path):
    dest = tmp_path / "out"
    fetch_nvim.unpack(_tar(tmp_path / "a.tar.gz", NVIM_TREE), LINUX, dest)
    assert (dest / "bin" / "nvim").is_file()
    assert (dest / "lib" / "nvim" / "parser" / "c.so").is_file()
    assert (dest / "share" / "nvim" / "runtime" / "init.lua").is_file()
    assert not (dest / "share" / "man").exists()


@NEEDS_TAR_FILTERS
def test_unpack_refuses_links_that_leave_the_archive(tmp_path):
    archive = _tar(
        tmp_path / "evil.tar.gz",
        NVIM_TREE,
        links=[
            ("nvim-linux-x86_64/lib/nvim/d", "../.."),
            ("nvim-linux-x86_64/lib/nvim/d/e", "../../../../.."),
        ],
    )
    with pytest.raises(tarfile.FilterError):
        fetch_nvim.unpack(archive, LINUX, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unpack_a_windows_zip(tmp_path):
    windows = PLATFORMS["windows-x86_64"]
    archive = tmp_path / "w.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nvim-win64/bin/nvim.exe", b"MZ")
        zf.writestr("nvim-win64/share/nvim/runtime/init.lua", b"--")
    fetch_nvim.unpack(archive, windows, tmp_path / "out")
    assert (tmp_path / "out" / "bin" / "nvim.exe").read_bytes() == b"MZ"


def test_an_archive_that_does_not_match_its_pin_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path))

    def evil(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"evil")

    monkeypatch.setattr(fetch_nvim, "download", evil)
    with pytest.raises(SystemExit, match="checksum mismatch"):
        fetch_nvim.archive(RELEASES["0.12"], LINUX)
    assert not list(tmp_path.rglob("*.tar.gz"))


def test_a_verified_cached_archive_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path))
    data = b"archive"
    digest = hashlib.sha256(data).hexdigest()
    pinned = replace(RELEASES["0.12"], sha256={LINUX.key: digest})
    cached = tmp_path / digest[:16] / LINUX.asset
    cached.parent.mkdir()
    cached.write_bytes(data)
    monkeypatch.setattr(fetch_nvim, "download", lambda url, dest: pytest.fail("downloaded"))
    assert fetch_nvim.archive(pinned, LINUX) == cached


def test_the_two_repositories_never_share_a_cache_entry():
    """Both publish v0.11.7/nvim-linux-x86_64.tar.gz; the files differ."""
    main, old = RELEASES["0.11"], OLD_GLIBC_RELEASES["0.11"]
    assert main.url(LINUX.key) != old.url(LINUX.key)
    assert main.sha256[LINUX.key][:16] != old.sha256[LINUX.key][:16]


# --------------------------------------------------------------------------
# check_wheels
# --------------------------------------------------------------------------
def fake_wheel(
    tmp_path, tag="manylinux_2_34_x86_64", glibc=b"GLIBC_2.34", runtime=1600, drop=(), version=None
):
    spec = WHEELS[tag]
    platform = PLATFORMS[spec.platform]
    path = tmp_path / f"fast_nvim_release-0.3.0-py3-none-{tag}.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for key, release in spec.releases.items():
            if key in drop:
                continue
            root = f"fast_nvim_release/_nvim/{key}/{platform.key}/"
            zf.writestr(f"{root}VERSION", (version or release.version) + "\n")
            exe = zipfile.ZipInfo(f"{root}bin/{platform.exe}")
            exe.external_attr = 0o755 << 16
            zf.writestr(exe, b"\x7fELF " + glibc)
            for n in range(runtime):
                zf.writestr(f"{root}share/nvim/runtime/f{n}.vim", b"")
            zf.writestr(f"fast_nvim_release/_nvim/LICENSE.nvim-{key}.txt", b"Apache")
        info = "fast_nvim_release-0.3.0.dist-info/"
        tags = "".join(f"Tag: py3-none-{part}\n" for part in tag.split("."))
        zf.writestr(info + "WHEEL", f"Root-Is-Purelib: false\n{tags}")
        zf.writestr(info + "entry_points.txt", "[console_scripts]\n")
    return path


@pytest.mark.parametrize("tag", sorted(WHEELS))
def test_a_complete_wheel_passes(tmp_path, tag):
    glibc = b"GLIBC_2.17" if "2_17" in tag else b"GLIBC_2.34"
    assert check_wheels.check(fake_wheel(tmp_path, tag=tag, glibc=glibc)) == []


def test_the_old_glibc_wheel_must_not_need_a_newer_glibc(tmp_path):
    tag = "manylinux_2_17_x86_64.manylinux2014_x86_64"
    problems = check_wheels.check(fake_wheel(tmp_path, tag=tag, glibc=b"GLIBC_2.34"))
    assert any("needs glibc (2, 34)" in item for item in problems), problems


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"glibc": b"GLIBC_2.38"}, "needs glibc (2, 38)"),
        ({"runtime": 10}, "only 10 runtime files"),
        ({"drop": ("0.11",)}, "expected 0.10/linux-x86_64"),
        ({"version": "v0.12.5"}, "the pin is v0.12.6"),
    ],
)
def test_a_broken_wheel_fails(tmp_path, kwargs, problem):
    problems = check_wheels.check(fake_wheel(tmp_path, **kwargs))
    assert any(problem in item for item in problems), problems


def test_versions_must_match_the_tag(tmp_path, capsys):
    wheel = fake_wheel(tmp_path)
    assert check_wheels.main([str(wheel), "--expect-version", "0.3.0"]) == 0
    assert check_wheels.main([str(wheel), "--expect-version", "9.9.9"]) == 1


# --------------------------------------------------------------------------
# check_upstream
# --------------------------------------------------------------------------
def test_newest_patch_per_line():
    tags = ["v0.12.5", "v0.12.6", "v0.11.7", "nightly", "stable", "v0.12.10"]
    assert check_upstream.newest_patches(tags) == {"0.12": (0, 12, 10), "0.11": (0, 11, 7)}


def _versions(pins):
    return {
        key: tuple(int(n) for n in release.version[1:].split(".")) for key, release in pins.items()
    }


def test_report_flags_stale_pins(capsys):
    newest = {**_versions(RELEASES), "0.11": (0, 11, 8)}
    assert check_upstream.report(newest, RELEASES) == [
        "0.11: pinned v0.11.7, neovim/neovim publishes v0.11.8"
    ]


def test_the_old_glibc_pins_are_checked_against_their_own_repository(capsys):
    # neovim-releases catching up with 0.12.6 must flag the old-glibc pin
    newest = {**_versions(OLD_GLIBC_RELEASES), "0.12": (0, 12, 6)}
    assert check_upstream.report(newest, OLD_GLIBC_RELEASES) == [
        "0.12: pinned v0.12.5, neovim/neovim-releases publishes v0.12.6"
    ]


@pytest.mark.parametrize("pins", [RELEASES, OLD_GLIBC_RELEASES])
def test_report_is_quiet_when_everything_is_current(capsys, pins):
    assert check_upstream.report(_versions(pins), pins) == []


def test_a_newer_release_line_is_reported():
    newest = {**_versions(RELEASES), "0.13": (0, 13, 0)}
    assert check_upstream.newer_lines(newest) == ["Neovim has a newer release line: v0.13.0"]


def test_a_lagging_old_glibc_build_is_a_note_not_a_failure():
    assert check_upstream.lagging() == [
        "0.12: the old-glibc wheel ships v0.12.5, the others v0.12.6"
    ]
