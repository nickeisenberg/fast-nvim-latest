"""The platform/asset table must stay consistent and complete."""

from __future__ import annotations

import re

import pytest

from fast_nvim.platforms import (
    NVIM_VERSION,
    TARGETS,
    parse_target,
    host_target,
    normalize_arch,
    normalize_family,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_every_target_is_well_formed():
    for key, target in TARGETS.items():
        assert key == "{}-{}".format(target.family, target.arch)
        assert target.asset.endswith((".tar.gz", ".zip"))
        assert target.archive == ("zip" if target.asset.endswith(".zip") else "tar.gz")
        assert HEX64.match(target.sha256), key
        assert target.size > 1_000_000, key
        assert target.exe in ("nvim", "nvim.exe")
        assert target.exe.endswith(".exe") == (target.family == "windows")
        assert target.url.endswith("/{}/{}".format(NVIM_VERSION, target.asset)), (
            target.url
        )


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
