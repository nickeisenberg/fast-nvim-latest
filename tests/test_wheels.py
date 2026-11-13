"""Wheel validation and the vendoring script's bookkeeping.

``scripts/check_wheels.py`` is what gates a PyPI upload, so its checks are
tested against synthetic wheels: a validator that crashes or silently passes
is worse than none.
"""

from __future__ import annotations

import io
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from fast_nvim_release import __version__, bundle
from fast_nvim_release.platforms import (
    PLATFORMS,
    RELEASE_KEYS,
    RELEASES,
    Target,
    host_platform,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_wheels  # noqa: E402
import fetch_nvim  # noqa: E402

WIN = PLATFORMS["windows-x86_64"]  # no glibc/macOS floor to check


# --------------------------------------------------------------------------
# synthetic wheels
# --------------------------------------------------------------------------
def fake_wheel(
    tmp_path: Path,
    platform=WIN,
    *,
    releases=tuple(RELEASE_KEYS),
    version: str = __version__,
    with_license: bool = True,
    purelib: str = "false",
    runtime_files: int = 1501,
    entry_points: bool = True,
    payload_version: str = "",
    payload_sha: str = "",
    index_text: str = "",
) -> Path:
    path = tmp_path / f"fast_nvim_release-{version}-py3-none-{platform.wheel_tag}.whl"
    prefix = "fast_nvim_release/_nvim/"
    dist = f"fast_nvim_release-{version}.dist-info/"

    def add(name: str, data: bytes = b"", mode: int = 0o644) -> None:
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFREG | mode) << 16
        zf.writestr(info, data)

    with zipfile.ZipFile(path, "w") as zf:
        add("fast_nvim_release/__init__.py")
        for release_key in releases:
            target = RELEASES[release_key].target(platform.key)
            if target is None:
                continue
            root = f"{prefix}{release_key}/{platform.key}/"
            add(f"{root}bin/{target.exe}", b"MZ", 0o755)
            for index in range(runtime_files):
                add(f"{root}share/nvim/runtime/generated{index}.vim")
            for required in ("syntax/syntax.vim", "doc/tags", "filetype.lua"):
                add(f"{root}share/nvim/runtime/{required}")
            add(
                f"{root}BUILD_INFO.json",
                json.dumps(
                    {
                        "release": release_key,
                        "nvim_version": payload_version or target.nvim_version,
                        "sha256": payload_sha or target.sha256,
                    }
                ).encode(),
            )
            if with_license:
                add(f"{prefix}LICENSE.nvim-{release_key}.txt", b"Apache License 2.0\n")
        add(
            f"{prefix}BUILD_INFO.json",
            (
                index_text
                or json.dumps(
                    {"releases": {key: {"nvim_version": RELEASES[key].version} for key in releases}}
                )
            ).encode(),
        )
        add(
            f"{dist}WHEEL",
            f"Root-Is-Purelib: {purelib}\nTag: py3-none-{platform.wheel_tag}\n".encode(),
        )
        add(
            f"{dist}METADATA",
            b"Metadata-Version: 2.4\nDescription-Content-Type: text/markdown\n\n# x\n",
        )
        if entry_points:
            add(f"{dist}entry_points.txt", b"[console_scripts]\nfast-nvim-release = x\n")
    return path


# --------------------------------------------------------------------------
# check_wheels
# --------------------------------------------------------------------------
def test_a_good_wheel_passes(tmp_path):
    assert check_wheels.check(fake_wheel(tmp_path)) == []


def test_a_missing_license_is_an_error(tmp_path):
    problems = check_wheels.check(fake_wheel(tmp_path, with_license=False))
    assert any("LICENSE.nvim-0.12.txt" in problem for problem in problems)


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


def test_a_wheel_must_ship_the_platform_its_tag_names(tmp_path):
    """A win_amd64 wheel carrying no Windows payload is not installable."""
    path = tmp_path / f"fast_nvim_release-{__version__}-py3-none-win_amd64.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fast_nvim_release/__init__.py", b"")
        zf.writestr(
            f"fast_nvim_release-{__version__}.dist-info/WHEEL",
            b"Root-Is-Purelib: false\nTag: py3-none-win_amd64\n",
        )
        zf.writestr(
            f"fast_nvim_release-{__version__}.dist-info/METADATA",
            b"Description-Content-Type: text/markdown\n",
        )
    problems = check_wheels.check(path)
    assert any("carries no Neovim builds" in problem for problem in problems)


def test_the_index_has_to_mention_every_shipped_release(tmp_path):
    problems = check_wheels.check(
        fake_wheel(tmp_path, index_text=json.dumps({"releases": {"0.12": {}}}))
    )
    assert any("does not mention v0.10.4" in problem for problem in problems)


def test_a_wheel_that_skips_a_release_is_reported_as_a_note(tmp_path):
    """Partial wheels are allowed, but the gap must be visible in the report."""
    full = fake_wheel(tmp_path, releases=tuple(RELEASE_KEYS))
    assert check_wheels.missing_releases(full) == []

    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    partial = fake_wheel(partial_dir, PLATFORMS["windows-x86_64"], releases=(RELEASE_KEYS[-1],))
    assert check_wheels.missing_releases(partial) == list(RELEASE_KEYS[:-1])
    assert check_wheels.check(partial) == []  # still valid, just smaller

    # a platform 0.10 never had is not "missing"
    arm_dir = tmp_path / "arm"
    arm_dir.mkdir()
    arm = fake_wheel(arm_dir, PLATFORMS["windows-arm64"], releases=("0.11", "0.12"))
    assert check_wheels.missing_releases(arm) == []


def test_garbage_is_reported_not_raised(tmp_path):
    broken = tmp_path / f"fast_nvim_release-{__version__}-py3-none-{WIN.wheel_tag}.whl"
    broken.write_bytes(b"not a zip")
    problems = check_wheels.check(broken)
    assert problems and "not a readable wheel" in problems[0]


def test_unknown_names_and_tags_are_reported(tmp_path):
    assert check_wheels.check(tmp_path / "README.md")
    wrong = tmp_path / f"fast_nvim_release-{__version__}-py3-none-solaris_9_x86_64.whl"
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
    here = host_platform()
    if here is None:
        pytest.skip("unknown host platform")
    checked = 0
    for release_key in RELEASE_KEYS:
        target = RELEASES[release_key].target(here.key)
        if target is None:
            continue
        exe = bundle.bundle_exe(release_key, here.key)
        if not exe.is_file():
            continue
        data = exe.read_bytes()
        assert check_wheels.binary_arch(data) == here.arch, target.label
        kind, floor = check_wheels.tagged_min_version(here.wheel_tag)
        if kind == "glibc":
            assert check_wheels.glibc_floor(data) <= floor, target.label
        else:
            assert check_wheels.macho_min_os(data) <= floor, target.label
        checked += 1
    if not checked:
        pytest.skip("no vendored build for this platform")


def test_the_download_only_build_would_break_the_arm64_tag():
    """Guard the reason 0.10/linux-arm64 is *not* vendored: its glibc floor.

    Reads the binary straight out of the cached archive, so it works without
    vendoring (and skips when the archive has not been downloaded yet).
    """
    target = RELEASES["0.10"].target("linux-arm64")
    assert target is not None
    archive = bundle.cached_archive(target)
    if not archive.is_file():
        pytest.skip("0.10/linux-arm64 archive is not in the cache")

    with tarfile.open(archive) as tar:
        member = next(m for m in tar if m.isfile() and m.name.endswith("/bin/nvim"))
        data = tar.extractfile(member).read()

    floor = check_wheels.glibc_floor(data)
    kind, tag_floor = check_wheels.tagged_min_version(PLATFORMS["linux-arm64"].wheel_tag)
    assert kind == "glibc"
    assert floor is not None
    assert floor > tag_floor, (
        "0.10/linux-arm64 would fit the arm64 wheel tag after all; "
        "consider vendoring it in platforms.py"
    )


# --------------------------------------------------------------------------
# fetch_nvim bookkeeping
# --------------------------------------------------------------------------
def refresh_target() -> Target:
    """A target that never compares equal to a build we would vendor."""
    target = RELEASES["0.10"].target("linux-arm64")
    assert target is not None
    return target


def vendored_tree(root: Path, release_key: str, platform_key: str, version: str) -> Path:
    target = RELEASES[release_key].target(platform_key)
    assert target is not None
    final = root / release_key / platform_key
    (final / "bin").mkdir(parents=True)
    (final / "bin" / target.exe).write_text("#!/bin/sh\n", encoding="utf-8")
    (final / "BUILD_INFO.json").write_text(
        json.dumps({"release": release_key, "nvim_version": version, "sha256": target.sha256}),
        encoding="utf-8",
    )
    return final


def archive_for(target: Target, tmp_path: Path) -> Path:
    path = tmp_path / target.asset
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (
            (f"{target.strip_prefix}/bin/nvim", b"#!/bin/sh\n"),
            (f"{target.strip_prefix}/share/nvim/runtime/init.lua", b"-- lua\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.skipif(
    host_platform() is not None and host_platform().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_vendor_skips_an_up_to_date_build(tmp_path, monkeypatch):
    target = refresh_target()
    dest = tmp_path / "_nvim"
    vendored_tree(dest, "0.10", "linux-arm64", "v0.10.4")
    monkeypatch.setattr(
        fetch_nvim.bundle,
        "fetch_archive",
        lambda *a, **k: pytest.fail("should not re-fetch a current build"),
    )
    assert fetch_nvim.vendor(target, dest) == fetch_nvim.SKIPPED


@pytest.mark.skipif(
    host_platform() is not None and host_platform().key == "linux-arm64",
    reason="the fake binary would be executed on this host",
)
def test_vendor_refreshes_a_build_from_another_release(tmp_path, monkeypatch):
    target = refresh_target()
    dest = tmp_path / "_nvim"
    final = vendored_tree(dest, "0.10", "linux-arm64", "v0.0.1")
    (final / "share" / "nvim" / "runtime").mkdir(parents=True)
    (final / "share" / "nvim" / "runtime" / "stale.vim").write_text("old")
    monkeypatch.setattr(
        fetch_nvim.bundle, "fetch_archive", lambda *a, **k: archive_for(target, tmp_path)
    )
    assert fetch_nvim.vendor(target, dest) == fetch_nvim.VENDORED
    info = json.loads((final / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert info["nvim_version"] == "v0.10.4"
    assert info["release"] == "0.10"
    # the old tree is gone, not merged into the new one
    assert not (final / "share" / "nvim" / "runtime" / "stale.vim").exists()
    assert (final / "share" / "nvim" / "runtime" / "init.lua").is_file()


def test_needs_refresh_explains_itself(tmp_path):
    target = refresh_target()
    final = vendored_tree(tmp_path, "0.10", "linux-arm64", "v0.10.4")
    assert fetch_nvim._needs_refresh(final, target) is None

    (final / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": "v0.0.1", "sha256": target.sha256}),
        encoding="utf-8",
    )
    assert "v0.0.1" in fetch_nvim._needs_refresh(final, target)

    (final / "BUILD_INFO.json").write_text(
        json.dumps({"nvim_version": "v0.10.4", "sha256": "b" * 64}),
        encoding="utf-8",
    )
    assert "checksum" in fetch_nvim._needs_refresh(final, target)

    (final / "BUILD_INFO.json").unlink()
    assert fetch_nvim._needs_refresh(final, target)


def test_fetch_nvim_list_does_not_need_the_network(capsys):
    assert fetch_nvim.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "linux-x86_64" in out
    for key in RELEASE_KEYS:
        assert key in out
        assert RELEASES[key].version in out


def test_fetch_nvim_tolerates_a_platform_one_release_never_had(tmp_path, monkeypatch, capsys):
    """0.10 has no Windows-on-ARM build; `--platform` for a whole wheel must work.

    Vendoring itself is stubbed out: what matters here is which builds get
    selected, not the download (which would need the network).
    """
    seen = []

    def fake_vendor(target, dest_root, *, offline=False, force=False):
        seen.append(target.label)
        return fetch_nvim.SKIPPED

    monkeypatch.setattr(fetch_nvim, "vendor", fake_vendor)
    code = fetch_nvim.main(["--platform", "windows-arm64", "--dest", str(tmp_path), "--offline"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "skipping 0.10/windows-arm64" in out
    assert seen == ["0.11/windows-arm64", "0.12/windows-arm64"]


def test_fetch_nvim_fails_when_you_explicitly_ask_for_an_impossible_build(tmp_path, capsys):
    code = fetch_nvim.main(
        ["--release", "0.10", "--platform", "windows-arm64", "--dest", str(tmp_path), "--offline"]
    )
    assert code == 1
    assert "has no windows-arm64 build" in capsys.readouterr().err


def test_fetch_nvim_skips_download_only_builds(tmp_path, capsys):
    """--all must not quietly vendor 0.10/linux-arm64 and break the tag."""
    code = fetch_nvim.main(
        ["--release", "0.10", "--platform", "linux-arm64", "--dest", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "skipping 0.10/linux-arm64" in out
    assert not (tmp_path / "0.10" / "linux-arm64" / "bin").exists()


def test_expect_version_gate(tmp_path, capsys):
    good = fake_wheel(tmp_path, version=__version__)
    assert check_wheels.main(["--expect-version", __version__, str(good)]) == 0
    assert check_wheels.main(["--expect-version", "9.9.9", str(good)]) == 1
    assert "expected version 9.9.9" in capsys.readouterr().out


def test_expect_version_catches_an_empty_release(tmp_path, capsys):
    """dist/*.whl with no match must fail, not silently pass."""
    assert check_wheels.main(["--expect-version", "1.0", str(tmp_path / "*.whl")]) == 1


# --------------------------------------------------------------------------
# build_wheels
# --------------------------------------------------------------------------
def test_build_wheels_carries_the_tracked_licences_into_the_work_copy(tmp_path):
    """`--offline` builds must not fail because `_nvim` was not copied."""
    import build_wheels

    work = tmp_path / "fast-nvim-release"
    build_wheels._preserve_tracked_nvim_files(work)
    source = SCRIPTS.parent / "src" / "fast_nvim_release" / "_nvim"
    copied = list((work / "src" / "fast_nvim_release" / "_nvim").glob("LICENSE.nvim-*.txt"))
    assert copied, "no licences copied into the work tree"
    for path in copied:
        assert path.read_bytes() == (source / path.name).read_bytes()


# --------------------------------------------------------------------------
# the name is written down in five places; keep them agreeing
# --------------------------------------------------------------------------
def test_the_project_name_is_consistent_everywhere():
    import importlib
    import re

    from fast_nvim_release import cli

    pyproject = (SCRIPTS.parent / "pyproject.toml").read_text(encoding="utf-8")
    name = re.search(r'^name = "([^"]+)"', pyproject, re.M).group(1)
    script = re.search(
        r'^([\w.-]+) = "([\w.]+):main"', pyproject.split("[project.scripts]")[1], re.M
    )
    entry_point, module = script.groups()

    assert name == "fast-nvim-release"
    assert entry_point == cli.PROG == name
    package, _, attribute = module.rpartition(".")
    assert attribute == "cli"
    assert importlib.import_module(package).__name__ == package
    assert callable(importlib.import_module(module).main)
    # wheel file names use the normalised (underscored) form
    assert check_wheels.WHEEL_NAME.match(
        f"{name.replace('-', '_')}-0.1.0-py3-none-{WIN.wheel_tag}.whl"
    )


def test_the_cache_and_marker_directories_are_named_after_the_project():
    assert bundle.cache_root().name == "fast-nvim-release"
    assert bundle.MARKER_DIR.as_posix() == "share/fast-nvim-release"
    assert "fast-nvim-release" in bundle.USER_AGENT


def test_version_output_is_aligned_and_uses_the_real_program_name(capsys):
    import re

    from fast_nvim_release import cli

    assert cli.main(["version"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert lines[0].startswith(f"{cli.PROG} ")
    starts = set()
    for line in lines[:6]:
        match = re.match(r".*?\s{2,}(\S.*)$", line)
        assert match, line
        starts.add(match.start(1))
    assert len(starts) == 1, lines  # every value starts in the same column


# --------------------------------------------------------------------------
# the workflows have to fire at all, and only publish on purpose
# --------------------------------------------------------------------------
def _workflow(name: str) -> str:
    return (SCRIPTS.parent / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _triggers(text: str) -> str:
    return text.split("on:", 1)[1].split("\njobs:", 1)[0]


def test_ci_runs_on_the_branch_this_repo_uses():
    triggers = _triggers(_workflow("ci.yml"))
    # the default branch is master; a filter naming only `main` silently
    # stops every push from building anything
    assert "master" in triggers.split("branches:", 1)[1].splitlines()[0]
    assert "pull_request" in triggers


def test_only_a_version_tag_can_trigger_a_real_publish():
    release = _workflow("release.yml")
    assert 'tags: ["v*"]' in _triggers(release)
    # the branch push must not be a release trigger at all
    assert "branches:" not in _triggers(release)
    assert "startsWith(github.ref, 'refs/tags/v')" in release
    assert '"${GITHUB_REF_NAME#v}"' in release


def test_ci_never_publishes_anything():
    ci = _workflow("ci.yml")
    for forbidden in ("pypi-publish", "id-token", "twine upload", "environment:"):
        assert forbidden not in ci
    # and it does not build the six-wheel set; that is the release's job
    assert "build_wheels.py --all" not in ci


def test_a_release_is_never_cancelled_by_concurrency():
    assert "cancel-in-progress: false" in _workflow("release.yml")


def test_the_rehearsal_runs_the_same_gate_as_the_real_upload():
    release = _workflow("release.yml")
    rehearsal = release.split("publish-testpypi:", 1)[1].split("publish-pypi:", 1)[0]
    assert "check_wheels.py" in rehearsal
    real = release.split("publish-pypi:", 1)[1]
    assert "check_wheels.py" in real


def test_ci_failure_annotations_are_extracted_from_a_log(tmp_path, capsys):
    import annotate_ci_failures

    log = tmp_path / "pytest.log"
    log.write_text(
        "some progress\n"
        "FAILED tests/test_cli.py::test_x - AssertionError: nope\n"
        "noise\n"
        "E   RuntimeError: boom\n",
        encoding="utf-8",
    )
    assert annotate_ci_failures.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "::error::[pytest.log] FAILED tests/test_cli.py::test_x" in out
    assert "::error::[pytest.log] E   RuntimeError: boom" in out


def test_ci_annotations_fall_back_to_the_tail_of_the_log(tmp_path, capsys):
    import annotate_ci_failures

    log = tmp_path / "validate.log"
    log.write_text("building...\nexit 1\n", encoding="utf-8")
    assert annotate_ci_failures.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "::error::[validate.log] exit 1" in out


def test_a_passing_log_produces_no_annotations(tmp_path, capsys):
    import annotate_ci_failures

    log = tmp_path / "pytest.log"
    log.write_text("140 passed, 24 skipped in 3.5s\n", encoding="utf-8")
    assert annotate_ci_failures.main([str(log)]) == 0
    assert "::error::" not in capsys.readouterr().out


def test_a_windows_wheel_needs_no_posix_exec_bit(tmp_path):
    """zipfile on Windows records no 0o111, so demanding one was a false alarm."""
    path = fake_wheel(tmp_path, platform=PLATFORMS["windows-x86_64"])
    # rewrite the exe entries the way a Windows build would store them
    import shutil

    rebuilt = tmp_path / path.name
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(str(rebuilt) + ".new", "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith("/bin/nvim.exe"):
                item.external_attr = 0o666 << 16
            dst.writestr(item, data)
    shutil.move(str(rebuilt) + ".new", path)
    assert check_wheels.check(path) == []
