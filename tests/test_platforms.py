"""The release/platform tables must stay consistent and complete."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from unittest import mock

import pytest

from fast_nvim_release import platforms
from fast_nvim_release.platforms import (
    DEFAULT_RELEASE,
    PLATFORM_KEYS,
    PLATFORMS,
    RELEASE_KEYS,
    RELEASES,
    parse_release,
    parse_target,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_every_release_is_well_formed():
    for key, release in RELEASES.items():
        assert key in RELEASE_KEYS
        assert release.key == key
        assert re.match(r"^v\d+\.\d+\.\d+$", release.version), key
        assert release.version.startswith(f"v{key}."), key
        for target in release.targets:
            assert HEX64.match(target.sha256), target.label
            assert target.size > 1_000_000, target.label
            assert target.exe in ("nvim", "nvim.exe")
            assert target.exe.endswith(".exe") == (target.family == "windows")
            assert target.url.endswith(f"/{release.version}/{target.asset}"), target.url


def test_the_pinned_version_table_cannot_drift():
    assert platforms.RELEASE_VERSIONS == {key: release.version for key, release in RELEASES.items()}
    assert RELEASE_KEYS == ["0.10", "0.11", "0.12"]
    assert DEFAULT_RELEASE == "0.12"


def test_every_release_supports_the_common_platforms():
    must_have = {"linux-x86_64", "linux-arm64", "macos-x86_64", "macos-arm64", "windows-x86_64"}
    for key, release in RELEASES.items():
        assert must_have <= set(release.assets), key
    # Neovim only started publishing Windows-on-ARM builds in 0.11
    assert "windows-arm64" not in RELEASES["0.10"].assets
    assert "windows-arm64" in RELEASES["0.11"].assets
    assert "windows-arm64" in RELEASES["0.12"].assets


def test_missing_platforms_are_explained():
    assert "windows-arm64" in RELEASES["0.10"].missing
    assert "no windows-arm64 build" in RELEASES["0.10"].platform_note("windows-arm64")
    assert RELEASES["0.12"].platform_note("windows-arm64") is None


def test_the_arm64_glibc_outlier_is_not_bundled():
    """0.10's arm64 build needs glibc 2.38, which would raise the wheel tag."""
    assert RELEASES["0.10"].downloaded_not_bundled == ("linux-arm64",)
    assert not RELEASES["0.10"].bundled("linux-arm64")
    assert RELEASES["0.10"].bundled("linux-x86_64")
    assert "glibc" in (RELEASES["0.10"].platform_note("linux-arm64") or "")
    for key in ("0.11", "0.12"):
        assert RELEASES[key].downloaded_not_bundled == ()


def test_the_glibc_floor_of_the_download_only_build_is_recorded():
    """The floor is why it is not bundled, so every downloaded_not_bundled
    build must have one: without it we would download a binary that cannot
    start on the systems the wheel still supports."""
    for key, release in RELEASES.items():
        for platform_key in release.downloaded_not_bundled:
            assert platform_key in release.glibc_floors, (key, platform_key)
            assert release.glibc_floors[platform_key] > (2, 34), (key, platform_key)

    assert RELEASES["0.10"].target("linux-arm64").min_glibc == (2, 38)
    assert RELEASES["0.12"].target("linux-arm64").min_glibc is None
    assert "2.38" in (RELEASES["0.10"].platform_note("linux-arm64") or "")


def test_a_build_is_refused_when_this_glibc_is_too_old(monkeypatch):
    """0.10 arm64 on Ubuntu 22.04 / Debian 12 / RHEL 9 must fail up front."""
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 35))
    problem = platforms.glibc_problem(target)
    assert problem is not None
    assert "glibc 2.38" in problem and "2.35" in problem
    assert f"setup {DEFAULT_RELEASE}" in problem


def test_the_suggestion_skips_a_line_that_needs_more_glibc(monkeypatch):
    """Suggest a line this system can run, not blindly the newest one."""
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 35))
    patched = dict(RELEASES)
    patched[RELEASE_KEYS[-1]] = replace(
        RELEASES[RELEASE_KEYS[-1]], glibc_floors={"linux-arm64": (2, 38)}
    )
    monkeypatch.setattr(platforms, "RELEASES", patched)
    problem = platforms.glibc_problem(target) or ""
    assert f"setup {RELEASE_KEYS[-2]}" in problem


def test_the_suggestion_falls_back_to_the_default_when_nothing_fits(monkeypatch):
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 35))
    patched = {
        key: replace(release, glibc_floors=dict(release.glibc_floors, **{"linux-arm64": (2, 38)}))
        for key, release in RELEASES.items()
    }
    monkeypatch.setattr(platforms, "RELEASES", patched)
    problem = platforms.glibc_problem(target) or ""
    assert f"setup {DEFAULT_RELEASE}" in problem


@pytest.mark.parametrize("have", [(2, 38), (2, 39), (3, 0)])
def test_a_build_is_allowed_when_this_glibc_is_new_enough(monkeypatch, have):
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: have)
    assert platforms.glibc_problem(target) is None


def test_an_unknown_glibc_never_blocks_an_install(monkeypatch):
    """musl, a stub libc or a failed probe must not guess "too old"."""
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: None)
    assert platforms.glibc_problem(target) is None


def test_the_glibc_check_only_applies_to_this_platform(monkeypatch):
    """Cross-installing another platform's build says nothing about our glibc."""
    target = RELEASES["0.10"].target("linux-arm64")
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-x86_64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 17))
    assert platforms.glibc_problem(target) is None


def test_releases_without_a_floor_are_never_a_problem(monkeypatch):
    monkeypatch.setattr(platforms, "host_platform", lambda: PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 17))
    assert platforms.glibc_problem(RELEASES["0.12"].target("linux-arm64")) is None


def test_host_glibc_parses_what_confstr_reports(monkeypatch):
    monkeypatch.setattr(platforms, "_host_family", lambda: "linux")
    monkeypatch.setattr(platforms.os, "confstr", lambda name: "glibc 2.34")
    assert platforms.host_glibc() == (2, 34)

    monkeypatch.setattr(platforms.os, "confstr", lambda name: "musl 1.2.4")
    assert platforms.host_glibc() == (1, 2)

    def explode(name):
        raise ValueError("no such confstr")

    monkeypatch.setattr(platforms.os, "confstr", explode)
    assert platforms.host_glibc() is None


def test_host_glibc_is_posix_only(monkeypatch):
    monkeypatch.setattr(platforms, "_host_family", lambda: "macos")
    assert platforms.host_glibc() is None


def test_wheel_tags_are_unique_and_cover_every_platform():
    tags = [platform.wheel_tag for platform in PLATFORMS.values()]
    assert len(set(tags)) == len(tags)
    assert set(PLATFORMS) == set(PLATFORM_KEYS)
    assert PLATFORMS["linux-x86_64"].wheel_tag == "manylinux_2_34_x86_64"
    assert PLATFORMS["linux-arm64"].wheel_tag == "manylinux_2_34_aarch64"
    assert PLATFORMS["macos-x86_64"].wheel_tag == "macosx_11_0_x86_64"
    assert PLATFORMS["macos-arm64"].wheel_tag == "macosx_11_0_arm64"
    assert PLATFORMS["windows-x86_64"].wheel_tag == "win_amd64"
    assert PLATFORMS["windows-arm64"].wheel_tag == "win_arm64"


# --------------------------------------------------------------------------
# release parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec, expected",
    [
        ("0.10", "0.10"),
        ("10", "0.10"),
        ("010", "0.10"),
        ("v0.10.4", "0.10"),
        ("0.10.4", "0.10"),
        ("0.11", "0.11"),
        ("0.12", "0.12"),
        ("12", "0.12"),
        ("V0.12.6", "0.12"),
        ("latest", "0.12"),
        ("newest", "0.12"),
        ("", "0.12"),
        (None, "0.12"),
        ("auto", "0.12"),
    ],
)
def test_release_parsing(spec, expected):
    assert parse_release(spec) == expected


def test_unknown_releases_raise_helpfully():
    with pytest.raises(ValueError) as excinfo:
        parse_release("0.9")
    assert "0.10" in str(excinfo.value)
    with pytest.raises(ValueError):
        parse_release("banana")
    # a patch that is not the pinned one is not silently accepted
    with pytest.raises(ValueError):
        parse_release("0.12.1")


# --------------------------------------------------------------------------
# platform parsing
# --------------------------------------------------------------------------
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
    assert target.key in PLATFORMS


def test_explicit_arch_wins_for_other_families():
    assert parse_target("windows", "arm64").key == "windows-arm64"
    assert parse_target("mac", "x86_64").key == "macos-x86_64"
    assert parse_target("linux", "aarch64").key == "linux-arm64"


def test_the_release_is_carried_on_the_target():
    target = parse_target("linux", "x86_64", "0.10")
    assert target.release_key == "0.10"
    assert target.nvim_version == "v0.10.4"
    assert target.label == "0.10/linux-x86_64"
    assert target.sha256 == RELEASES["0.10"].assets["linux-x86_64"][0]


def test_a_release_without_a_platform_says_so():
    with pytest.raises(ValueError) as excinfo:
        parse_target("windows", "arm64", "0.10")
    assert "no windows-arm64 build" in str(excinfo.value)


def test_conflicting_arch_is_an_error():
    with pytest.raises(ValueError):
        parse_target("linux-arm64", "x86_64")


def test_unknown_names_raise_helpful_errors():
    with pytest.raises(ValueError) as excinfo:
        parse_target("plan9")
    assert "linux" in str(excinfo.value)
    with pytest.raises(ValueError):
        platforms.normalize_arch("sparc")
    with pytest.raises(ValueError):
        platforms.normalize_family("haiku")


def test_auto_uses_this_machine():
    here = platforms.host_platform()
    if here is None:  # pragma: no cover
        pytest.skip("unknown host")
    target = parse_target(None)
    assert target.key == here.key
    assert target.release_key == DEFAULT_RELEASE
    assert parse_target("auto").key == here.key
    assert parse_target("host").key == here.key


def test_host_target_speaks_for_the_requested_release():
    here = platforms.host_platform()
    if here is None:  # pragma: no cover
        pytest.skip("unknown host")
    assert platforms.host_target().release_key == DEFAULT_RELEASE
    if RELEASES["0.10"].target(here.key) is not None:
        assert platforms.host_target("0.10").release_key == "0.10"
    assert platforms.is_cross(platforms.host_target()) is False


def test_cross_platform_is_decided_per_platform_not_per_release():
    here = platforms.host_platform()
    if here is None:  # pragma: no cover
        pytest.skip("unknown host")
    # another release on this machine is not "cross"
    assert platforms.is_cross(platforms.host_target("0.10")) is False


# --------------------------------------------------------------------------
# the host has to be *recognised*, not guessed
# --------------------------------------------------------------------------
def test_unknown_host_architectures_are_not_guessed(monkeypatch):
    monkeypatch.setattr(platforms.platform, "machine", lambda: "riscv64")
    assert platforms.host_platform() is None
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
    assert platforms.host_platform() is None


@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "x86-64", "em64t"])
def test_amd64_spellings_mean_x86_64(monkeypatch, machine):
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    here = platforms.host_platform()
    if here is None:  # pragma: no cover - exotic host
        pytest.skip("unknown host family")
    assert here.arch == "x86_64"


@pytest.mark.parametrize("machine", ["arm64", "aarch64"])
def test_arm_spellings_mean_arm64(monkeypatch, machine):
    monkeypatch.setattr(platforms.platform, "machine", lambda: machine)
    here = platforms.host_platform()
    if here is None:  # pragma: no cover - exotic host
        pytest.skip("unknown host family")
    assert here.arch == "arm64"


# --------------------------------------------------------------------------
# emulated interpreters: warn, never silently install the wrong thing
# --------------------------------------------------------------------------
def _emulation(system, machine, env=None, rosetta=False):
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
        mock.patch.object(platforms.sys, "platform", "darwin"),
        mock.patch.object(platforms.platform, "machine", lambda: "x86_64"),
    ):
        assert platforms.host_platform().key == "macos-x86_64"


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
