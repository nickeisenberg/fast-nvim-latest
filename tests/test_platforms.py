"""Platforms and host detection."""

from __future__ import annotations

import pytest

from fast_nvim_release import platforms
from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS


def test_archive_layout():
    assert PLATFORMS["linux-x86_64"].archive_dir == "nvim-linux-x86_64"
    assert PLATFORMS["windows-arm64"].archive_dir == "nvim-win-arm64"


def test_old_glibc_tags_also_name_manylinux2014_for_old_pip():
    for key, tag in OLD_GLIBC_TAGS.items():
        assert key.startswith("linux-")
        assert tag.startswith("manylinux_2_17_") and ".manylinux2014_" in tag


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


@pytest.mark.parametrize(
    "machine, glibc, tag",
    [
        ("x86_64", (2, 39), "manylinux_2_34_x86_64"),
        ("x86_64", (2, 34), "manylinux_2_34_x86_64"),
        ("x86_64", (2, 28), "manylinux_2_17_x86_64.manylinux2014_x86_64"),  # RHEL 8
        ("aarch64", (2, 28), "manylinux_2_17_aarch64.manylinux2014_aarch64"),
        ("x86_64", None, "manylinux_2_34_x86_64"),  # glibc unknown (e.g. musl)
    ],
)
def test_the_wheel_for_this_machine_follows_glibc_like_pip(monkeypatch, machine, glibc, tag):
    monkeypatch.setattr(platforms.sys, "platform", "linux")
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    monkeypatch.setattr(platforms, "host_glibc", lambda: glibc)
    assert platforms.host_wheel_tag() == tag


def test_glibc_does_not_matter_off_linux(monkeypatch):
    monkeypatch.setattr(platforms.sys, "platform", "darwin")
    monkeypatch.setattr(platforms.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 17))
    assert platforms.host_wheel_tag() == "macosx_11_0_arm64"
