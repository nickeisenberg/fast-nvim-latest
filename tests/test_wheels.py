"""Wheel validation and the vendoring script's bookkeeping.

``scripts/check_wheels.py`` is what gates a PyPI upload, so its checks are
tested against synthetic wheels: a validator that crashes or silently passes
is worse than none.
"""

from __future__ import annotations

import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from fast_nvim_latest import __version__, bundle
from fast_nvim_latest.platforms import NVIM_VERSION, TARGETS, Target, host_target

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_wheels  # noqa: E402
import fetch_nvim  # noqa: E402

TARGET = TARGETS["windows-x86_64"]  # no glibc/macOS floor to check


# --------------------------------------------------------------------------
# synthetic wheels
# --------------------------------------------------------------------------
def fake_wheel(
    tmp_path: Path,
    target: Target = TARGET,
    *,
    version: str = __version__,
    with_license: bool = True,
    payload_version: str = NVIM_VERSION,
    payload_sha: str = "",
    purelib: str = "false",
    runtime_files: int = 2001,
    entry_points: bool = True,
) -> Path:
    path = tmp_path / f"fast_nvim_latest-{version}-py3-none-{target.wheel_tag}.whl"
    prefix = "fast_nvim_latest/_nvim/"
    dist = f"fast_nvim_latest-{version}.dist-info/"

    def add(name: str, data: bytes = b"", mode: int = 0o644) -> None:
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFREG | mode) << 16
        zf.writestr(info, data)

    with zipfile.ZipFile(path, "w") as zf:
        add("fast_nvim_latest/__init__.py")
        add(f"{prefix}{target.key}/bin/{target.exe}", b"MZ", 0o755)
        for index in range(runtime_files):
            add(f"{prefix}{target.key}/share/nvim/runtime/generated{index}.vim")
        for required in ("syntax/syntax.vim", "doc/tags", "filetype.lua"):
            add(f"{prefix}{target.key}/share/nvim/runtime/{required}")
        add(
            f"{prefix}BUILD_INFO.json",
            json.dumps({"nvim_version": NVIM_VERSION}).encode(),
        )
        add(
            f"{prefix}{target.key}/BUILD_INFO.json",
            json.dumps(
                {
                    "nvim_version": payload_version,
                    "sha256": payload_sha or target.sha256,
                }
            ).encode(),
        )
        if with_license:
            add(f"{prefix}LICENSE.nvim.txt", b"Apache License 2.0\n")
        add(
            f"{dist}WHEEL",
            f"Root-Is-Purelib: {purelib}\nTag: py3-none-{target.wheel_tag}\n".encode(),
        )
        add(
            f"{dist}METADATA",
            b"Metadata-Version: 2.4\nDescription-Content-Type: text/markdown\n\n# x\n",
        )
        if entry_points:
            add(f"{dist}entry_points.txt", b"[console_scripts]\nfast-nvim-latest = x\n")
    return path


# --------------------------------------------------------------------------
# check_wheels
# --------------------------------------------------------------------------
def test_a_good_wheel_passes(tmp_path):
    assert check_wheels.check(fake_wheel(tmp_path)) == []


def test_a_missing_license_is_an_error(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, with_license=False))
    assert any("LICENSE.nvim.txt" in problem for problem in problems)


def test_a_payload_from_another_release_is_an_error(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, payload_version="v0.0.1"))
    assert any("the pin says" in problem for problem in problems)


def test_a_repinned_checksum_is_an_error(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, payload_sha="0" * 64))
    assert any("checksum" in problem for problem in problems)


def test_purelib_and_missing_entry_point_are_errors(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, purelib="true", entry_points=False))
    assert any("Root-Is-Purelib" in problem for problem in problems)
    assert any("entry point" in problem for problem in problems)


def test_a_wheel_without_a_runtime_is_an_error(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, runtime_files=0))
    assert any("$VIMRUNTIME" in problem for problem in problems)


def test_garbage_is_reported_not_raised(tmp_path):
    broken = tmp_path / f"fast_nvim_latest-{__version__}-py3-none-{TARGET.wheel_tag}.whl"
    broken.write_bytes(b"not a zip")
    problems = check_wheels.check(broken)
    assert problems and "not a readable wheel" in problems[0]


def test_unknown_names_and_tags_are_reported(tmp_path):
    assert check_wheels.check(tmp_path / "README.md")
    wrong = tmp_path / f"fast_nvim_latest-{__version__}-py3-none-solaris_9_x86_64.whl"
    wrong.write_bytes(b"")
    assert "unknown platform tag" in check_wheels.check(wrong)[0]


def test_mixed_versions_in_one_directory_fail(tmp_path, capsys):
    first = fake_wheel(tmp_path, version=__version__)
    second = fake_wheel(tmp_path, version="9.9.9")
    assert check_wheels.main([str(first), str(second)]) == 1
    assert "different versions" in capsys.readouterr().out


# --------------------------------------------------------------------------
# tag vs. binary helpers
# --------------------------------------------------------------------------
def test_tag_parsing():
    assert check_wheels.tagged_arch("manylinux_2_34_aarch64") == "arm64"
    assert check_wheels.tagged_arch("win_amd64") == "x86_64"
    assert check_wheels.tagged_min_version("manylinux_2_34_x86_64") == (
        "glibc",
        (2, 34),
    )
    assert check_wheels.tagged_min_version("macosx_11_0_arm64") == ("macos", (11, 0))
    assert check_wheels.tagged_min_version("win_amd64") is None


def test_real_binaries_match_their_tags():
    """The pinned tags are only useful if the binaries agree with them."""
    host = host_target()
    if host is None:
        pytest.skip("unknown host platform")
    exe = bundle.bundle_exe(host.key)
    if not exe.is_file():
        pytest.skip("no vendored build for this platform")
    data = exe.read_bytes()
    assert check_wheels.binary_arch(data) == host.arch
    kind, floor = check_wheels.tagged_min_version(host.wheel_tag)
    if kind == "glibc":
        assert check_wheels.glibc_floor(data) <= floor
    else:
        assert check_wheels.macho_min_os(data) <= floor


# --------------------------------------------------------------------------
# fetch_nvim bookkeeping
# --------------------------------------------------------------------------
def refresh_target() -> Target:
    """A target that never compares equal to the host's real build."""
    return Target(
        key="linux-arm64",
        family="linux",
        arch="arm64",
        asset="fake-linux-arm64.tar.gz",
        archive="tar.gz",
        strip_prefix="fake-root",
        sha256="a" * 64,
        size=1,
        exe="nvim",
        wheel_tag="manylinux_2_34_aarch64",
    )


def vendored_tree(root: Path, target: Target, version: str) -> Path:
    final = root / target.key
    (final / "bin").mkdir(parents=True)
    (final / "bin" / target.exe).write_text("#!/bin/sh\n", encoding="utf-8")
    (final / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": version, "sha256": target.sha256}),
        encoding="utf-8",
    )
    return final


def archive_for(target: Target, tmp_path: Path) -> Path:
    path = tmp_path / target.asset
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (
            ("fake-root/bin/nvim", b"#!/bin/sh\n"),
            ("fake-root/share/nvim/runtime/init.lua", b"-- lua\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, __import__("io").BytesIO(data))
    return path


@pytest.mark.skipif(
    host_target() is not None and host_target().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_vendor_skips_an_up_to_date_build(tmp_path, monkeypatch):
    target = refresh_target()
    dest = tmp_path / "_nvim"
    vendored_tree(dest, target, NVIM_VERSION)
    monkeypatch.setattr(
        fetch_nvim.bundle,
        "fetch_archive",
        lambda *a, **k: pytest.fail("should not re-fetch a current build"),
    )
    assert fetch_nvim.vendor(target, dest) == fetch_nvim.SKIPPED


@pytest.mark.skipif(
    host_target() is not None and host_target().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_vendor_refreshes_a_build_from_another_release(tmp_path, monkeypatch):
    target = refresh_target()
    dest = tmp_path / "_nvim"
    final = vendored_tree(dest, target, "v0.0.1")
    (final / "share" / "nvim" / "runtime").mkdir(parents=True)
    (final / "share" / "nvim" / "runtime" / "stale.vim").write_text("old")
    monkeypatch.setattr(
        fetch_nvim.bundle, "fetch_archive", lambda *a, **k: archive_for(target, tmp_path)
    )
    assert fetch_nvim.vendor(target, dest) == fetch_nvim.VENDORED
    info = json.loads((final / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert info["nvim_version"] == NVIM_VERSION
    # the old tree is gone, not merged into the new one
    assert not (final / "share" / "nvim" / "runtime" / "stale.vim").exists()
    assert (final / "share" / "nvim" / "runtime" / "init.lua").is_file()


@pytest.mark.skipif(
    host_target() is not None and host_target().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_main_exits_zero_when_everything_is_already_vendored(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAST_NVIM_LATEST_CACHE_DIR", str(tmp_path / "cache"))
    target = TARGETS["linux-arm64"]
    dest = tmp_path / "_nvim"
    vendored_tree(dest, target, NVIM_VERSION)
    (dest / "LICENSE.nvim.txt").write_text("Apache License 2.0\n", encoding="utf-8")

    assert fetch_nvim.main(["--platform", target.key, "--dest", str(dest)]) == 0
    out = capsys.readouterr().out
    assert "already vendored" in out
    assert "next: python -m build --wheel" in out


@pytest.mark.skipif(
    host_target() is not None and host_target().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_main_fails_when_the_licence_cannot_be_shipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAST_NVIM_LATEST_CACHE_DIR", str(tmp_path / "cache"))
    target = TARGETS["linux-arm64"]
    dest = tmp_path / "_nvim"
    vendored_tree(dest, target, NVIM_VERSION)

    code = fetch_nvim.main(["--platform", target.key, "--dest", str(dest), "--offline"])
    assert code == 1
    assert "LICENSE.txt is missing" in capsys.readouterr().err


def test_needs_refresh_explains_itself(tmp_path):
    target = refresh_target()
    final = vendored_tree(tmp_path, target, NVIM_VERSION)
    assert fetch_nvim._needs_refresh(final, target) is None

    (final / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": "v0.0.1", "sha256": target.sha256}),
        encoding="utf-8",
    )
    assert "v0.0.1" in fetch_nvim._needs_refresh(final, target)

    (final / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": NVIM_VERSION, "sha256": "b" * 64}),
        encoding="utf-8",
    )
    assert "checksum" in fetch_nvim._needs_refresh(final, target)

    (final / "BUILD_INFO.json").unlink()
    assert fetch_nvim._needs_refresh(final, target)


def test_fetch_nvim_list_does_not_need_the_network(capsys):
    assert fetch_nvim.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "linux-x86_64" in out
    assert NVIM_VERSION in out


def test_expect_version_gate(tmp_path, capsys):
    good = fake_wheel(tmp_path, version=__version__)
    assert check_wheels.main(["--expect-version", __version__, str(good)]) == 0
    assert check_wheels.main(["--expect-version", "9.9.9", str(good)]) == 1
    assert "expected version 9.9.9" in capsys.readouterr().out


def test_expect_version_catches_an_empty_release(tmp_path, capsys):
    """dist/*.whl with no match must fail, not silently pass."""
    assert check_wheels.main(["--expect-version", "1.0", str(tmp_path / "*.whl")]) == 1


# --------------------------------------------------------------------------
# the name is written down in five places; keep them agreeing
# --------------------------------------------------------------------------
def test_the_project_name_is_consistent_everywhere():
    import importlib
    import re

    from fast_nvim_latest import cli

    pyproject = (SCRIPTS.parent / "pyproject.toml").read_text(encoding="utf-8")
    name = re.search(r'^name = "([^"]+)"', pyproject, re.M).group(1)
    script = re.search(
        r'^([\w.-]+) = "([\w.]+):main"', pyproject.split("[project.scripts]")[1], re.M
    )
    entry_point, module = script.groups()

    assert name == "fast-nvim-latest"
    assert entry_point == cli.PROG == name
    package, _, attribute = module.rpartition(".")
    assert attribute == "cli"
    assert importlib.import_module(package).__name__ == package
    assert callable(importlib.import_module(module).main)
    # wheel file names use the normalised (underscored) form
    assert check_wheels.WHEEL_NAME.match(
        f"{name.replace('-', '_')}-0.1.0-py3-none-{TARGET.wheel_tag}.whl"
    )


def test_the_cache_and_marker_directories_are_named_after_the_project():
    assert bundle.cache_root().name == "fast-nvim-latest"
    assert bundle.MARKER_DIR.as_posix() == "share/fast-nvim-latest"
    assert "fast-nvim-latest" in bundle.USER_AGENT


def test_version_output_is_aligned_and_uses_the_real_program_name(capsys):
    import re

    from fast_nvim_latest import cli

    assert cli.main(["version"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert lines[0].startswith(f"{cli.PROG} ")
    starts = set()
    for line in lines:
        match = re.match(r".*?\s{2,}(\S.*)$", line)
        assert match, line
        starts.add(match.start(1))
    assert len(starts) == 1, lines  # every value starts in the same column
