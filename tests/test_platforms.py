"""The platform/asset table must stay consistent and complete."""

from __future__ import annotations

import re

import pytest

from fast_nvim_latest import platforms
from fast_nvim_latest.platforms import (
    NVIM_VERSION,
    TARGETS,
    host_target,
    normalize_arch,
    normalize_family,
    parse_target,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_every_target_is_well_formed():
    for key, target in TARGETS.items():
        assert key == f"{target.family}-{target.arch}"
        assert target.asset.endswith((".tar.gz", ".zip"))
        assert target.archive == ("zip" if target.asset.endswith(".zip") else "tar.gz")
        assert HEX64.match(target.sha256), key
        assert target.size > 1_000_000, key
        assert target.exe in ("nvim", "nvim.exe")
        assert target.exe.endswith(".exe") == (target.family == "windows")
        assert target.url.endswith(f"/{NVIM_VERSION}/{target.asset}"), target.url


def test_assets_and_hashes_are_unique():
    assets = [t.asset for t in TARGETS.values()]
    hashes = [t.sha256 for t in TARGETS.values()]
    assert len(set(assets)) == len(assets)
    assert len(set(hashes)) == len(hashes)


def test_all_platform_arch_combinations_are_covered():
    combos = {(t.family, t.arch) for t in TARGETS.values()}
    assert combos == {
        ("linux", "x86_64"),
        ("linux", "arm64"),
        ("macos", "x86_64"),
        ("macos", "arm64"),
        ("windows", "x86_64"),
        ("windows", "arm64"),
    }


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("linux", "linux"),
        ("Linux", "linux"),
        ("linux-x86_64", "linux"),
        ("linux/arm64", "linux"),
        ("gnu-linux-aarch64", "linux"),
        ("mac", "macos"),
        ("macos", "macos"),
        ("osx", "macos"),
        ("darwin", "macos"),
        ("win", "windows"),
        ("windows", "windows"),
        ("win64", "windows"),
        ("windows/amd64", "windows"),
    ],
)
def test_alias_parsing(spec, expected):
    target = parse_target(spec)
    assert target.family == expected
    assert target.key in TARGETS


def test_explicit_arch_wins_for_other_families():
    assert parse_target("windows", "arm64").key == "windows-arm64"
    assert parse_target("mac", "x86_64").key == "macos-x86_64"
    assert parse_target("linux", "aarch64").key == "linux-arm64"


def test_conflicting_arch_is_an_error():
    with pytest.raises(ValueError):
        parse_target("linux-arm64", "x86_64")


def test_unknown_names_raise_helpful_errors():
    with pytest.raises(ValueError) as excinfo:
        parse_target("plan9")
    assert "linux" in str(excinfo.value)
    with pytest.raises(ValueError):
        normalize_arch("sparc")
    with pytest.raises(ValueError):
        normalize_family("haiku")


def test_auto_uses_this_machine():
    host = host_target()
    if host is None:  # pragma: no cover
        pytest.skip("unknown host")
    assert parse_target(None) is host
    assert parse_target("auto") is host
    assert parse_target("host") is host


def test_key_collisions_are_impossible():
    assert len(TARGETS) == 6


def test_wheel_tags_are_unique_and_match_the_binaries():
    """Verified against the real binaries: glibc 2.34, macOS 11.0."""
    assert TARGETS["linux-x86_64"].wheel_tag == "manylinux_2_34_x86_64"
    assert TARGETS["linux-arm64"].wheel_tag == "manylinux_2_34_aarch64"
    assert TARGETS["macos-x86_64"].wheel_tag == "macosx_11_0_x86_64"
    assert TARGETS["macos-arm64"].wheel_tag == "macosx_11_0_arm64"
    assert TARGETS["windows-x86_64"].wheel_tag == "win_amd64"
    assert TARGETS["windows-arm64"].wheel_tag == "win_arm64"
    tags = [target.wheel_tag for target in TARGETS.values()]
    assert len(set(tags)) == len(tags)


# --------------------------------------------------------------------------
# the host has to be *recognised*, not guessed
# --------------------------------------------------------------------------
def test_unknown_host_architectures_are_not_guessed(monkeypatch):
    monkeypatch.setattr(platforms.platform, "machine", lambda: "riscv64")
    assert platforms.host_target() is None
    with pytest.raises(ValueError) as excinfo:
        platforms.parse_target(None)
    assert "cannot detect this platform" in str(excinfo.value)
    # an explicit platform still works, because the user said what they want
    assert platforms.parse_target("linux").key.startswith("linux-")


@pytest.mark.parametrize("machine", ["armv7l", "armv8l", "i686", "i386", "ppc64le", "unknown"])
def test_32_bit_and_unknown_machines_are_rejected(monkeypatch, machine):
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    if platforms._host_family() is None:  # pragma: no cover - exotic host
        pytest.skip("unknown host family")
    assert platforms.host_target() is None


@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "x86-64", "em64t"])
def test_amd64_spellings_mean_x86_64(monkeypatch, machine):
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    host = platforms.host_target()
    if host is None:  # pragma: no cover - exotic host
        pytest.skip("unknown host family")
    assert host.arch == "x86_64"


@pytest.mark.parametrize("machine", ["arm64", "aarch64"])
def test_arm_spellings_mean_arm64(monkeypatch, machine):
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    host = platforms.host_target()
    if host is None:  # pragma: no cover - exotic host
        pytest.skip("unknown host family")
    assert host.arch == "arm64"


# --------------------------------------------------------------------------
# emulated interpreters: warn, never silently install the wrong thing
# --------------------------------------------------------------------------
def _emulation(system, machine, env=None, rosetta=False):
    import os
    from unittest import mock

    with (
        mock.patch.object(platforms.sys, "platform", system),
        mock.patch.object(platforms.platform, "machine", lambda: machine),
        mock.patch.object(platforms, "_rosetta_active", lambda: rosetta),
        mock.patch.dict(os.environ, env or {}, clear=True),
    ):
        return platforms.emulation(), platforms.emulation_note()


def test_rosetta_python_on_apple_silicon_is_detected():
    found, note = _emulation("darwin", "x86_64", rosetta=True)
    assert found == ("x86_64", "arm64")
    assert "Rosetta 2" in note
    assert "arm64" in note
    # and the build we install matches the interpreter, like pip's wheel does
    with (
        __import__("unittest").mock.patch.object(platforms.sys, "platform", "darwin"),
        __import__("unittest").mock.patch.object(platforms.platform, "machine", lambda: "x86_64"),
    ):
        assert platforms.host_target().key == "macos-x86_64"


def test_x64_python_on_windows_arm64_is_detected():
    found, note = _emulation("win32", "AMD64", {"PROCESSOR_ARCHITEW6432": "ARM64"})
    assert found == ("x86_64", "arm64")
    assert "arm64" in note


@pytest.mark.parametrize(
    "system, machine, env, rosetta",
    [
        ("linux", "x86_64", {}, False),
        ("linux", "aarch64", {}, False),
        ("darwin", "arm64", {}, False),
        ("darwin", "arm64", {}, True),  # arm64 python, translated flag is moot
        ("darwin", "x86_64", {}, False),  # a real Intel Mac
        ("win32", "AMD64", {}, False),
        ("win32", "ARM64", {"PROCESSOR_ARCHITEW6432": "ARM64"}, False),
        ("win32", "AMD64", {"PROCESSOR_ARCHITEW6432": "AMD64"}, False),
    ],
)
def test_native_interpreters_are_not_warned_about(system, machine, env, rosetta):
    found, note = _emulation(system, machine, env, rosetta)
    assert found is None
    assert note is None


def test_rosetta_probe_is_cheap_and_cached(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "1\n"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(platforms.subprocess, "run", fake_run)
    monkeypatch.setattr(platforms, "_ROSETTA", {})
    assert platforms._rosetta_active() is True
    assert platforms._rosetta_active() is True
    assert len(calls) == 1  # one sysctl per process


def test_rosetta_probe_survives_a_missing_sysctl(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("no sysctl here")

    monkeypatch.setattr(platforms.subprocess, "run", boom)
    monkeypatch.setattr(platforms, "_ROSETTA", {})
    assert platforms._rosetta_active() is False
