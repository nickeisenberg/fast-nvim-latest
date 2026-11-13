"""The release/platform tables and the host detection."""

from __future__ import annotations

import re

import pytest

from fast_nvim_release import platforms
from fast_nvim_release.platforms import DEFAULT_RELEASE, PLATFORMS, RELEASES, parse_release


def test_the_default_is_the_newest_release():
    assert DEFAULT_RELEASE == "0.12"
    assert list(RELEASES) == sorted(RELEASES, key=lambda key: tuple(map(int, key.split("."))))


def test_every_pin_is_a_sha256_for_a_known_platform():
    for release in RELEASES.values():
        assert re.fullmatch(r"v\d+\.\d+\.\d+", release.version)
        for key, digest in release.sha256.items():
            assert key in PLATFORMS
            assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_the_builds_that_do_not_exist_are_not_pinned():
    assert "windows-arm64" not in RELEASES["0.10"].sha256
    assert "linux-arm64" not in RELEASES["0.10"].sha256  # glibc 2.38; see platforms.py


def test_archive_layout_and_urls():
    assert PLATFORMS["linux-x86_64"].archive_dir == "nvim-linux-x86_64"
    assert PLATFORMS["windows-arm64"].archive_dir == "nvim-win-arm64"
    assert RELEASES["0.12"].url("windows-x86_64") == (
        "https://github.com/neovim/neovim/releases/download/v0.12.6/nvim-win64.zip"
    )


@pytest.mark.parametrize(
    "spec, key",
    [
        (None, "0.12"),
        ("latest", "0.12"),
        ("0.10", "0.10"),
        ("v0.11", "0.11"),
        ("12", "0.12"),
        ("0.12.6", "0.12"),
        ("v0.10.4", "0.10"),
    ],
)
def test_parse_release(spec, key):
    assert parse_release(spec) == key


@pytest.mark.parametrize("spec", ["0.9", "0.12.5", "13", "nightly", "new"])
def test_unknown_releases_are_refused(spec):
    with pytest.raises(ValueError, match="unknown Neovim release"):
        parse_release(spec)


@pytest.mark.parametrize(
    "system, machine, key",
    [
        ("linux", "x86_64", "linux-x86_64"),
        ("linux", "aarch64", "linux-arm64"),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x86_64"),
        ("win32", "AMD64", "windows-x86_64"),
        ("win32", "ARM64", "windows-arm64"),
        ("linux", "armv7l", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_host_platform(monkeypatch, system, machine, key):
    monkeypatch.setattr(platforms.sys, "platform", system)
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    found = platforms.host_platform()
    assert (found.key if found else None) == key
