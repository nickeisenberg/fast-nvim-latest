"""Build-time scripts: lines.json, fetching, wheel checks, pins, releases."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import tarfile
import zipfile

import build_wheels
import check_wheels
import fetch_nvim
import lines
import pins
import pytest
import release_notes
from conftest import POSIX_ONLY

from fast_nvim_release.platforms import OLD_GLIBC_TAGS, PLATFORMS

LINUX = PLATFORMS["linux-x86_64"]
OLD_X86 = OLD_GLIBC_TAGS["linux-x86_64"]
NEEDS_TAR_FILTERS = pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"), reason="this Python has no tarfile filters"
)


# --------------------------------------------------------------------------
# lines.json
# --------------------------------------------------------------------------
def test_lines_json_is_well_formed():
    data = lines.load()
    assert list(data) == sorted(data, key=lines.line_key)
    for line, spec in data.items():
        assert re.fullmatch(r"\d+\.\d+", line) and spec["k"] >= 1
        for build in lines.builds(spec):
            assert re.fullmatch(rf"v{re.escape(line)}\.\d+", build.version)
            for key, digest in build.sha256.items():
                assert key in PLATFORMS and re.fullmatch(r"[0-9a-f]{64}", digest)
            if build.kind == "old_glibc":
                assert set(build.sha256) <= set(OLD_GLIBC_TAGS)


def test_every_wheel_has_its_line_version_and_tag():
    data = lines.load()
    for wheel in lines.wheels(data):
        assert wheel.version == f"{wheel.line}.{data[wheel.line]['k']}"
        expected = (
            wheel.platform.wheel_tag
            if wheel.build.kind == "official"
            else OLD_GLIBC_TAGS[wheel.platform.key]
        )
        assert wheel.tag == expected
    labels = [(w.line, w.tag) for w in lines.wheels(data)]
    assert len(labels) == len(set(labels))


def test_known_gaps_have_no_wheel():
    tags = {(w.line, w.tag) for w in lines.wheels()}
    assert ("0.10", "manylinux_2_34_aarch64") not in tags  # needs glibc 2.38
    assert ("0.10", "win_arm64") not in tags  # never published
    assert ("0.11", OLD_GLIBC_TAGS["linux-arm64"]) not in tags  # neovim-releases: 0.12.3+
    with pytest.raises(KeyError, match="has no build for win_arm64"):
        lines.find("0.10", "win_arm64")


def test_the_two_repositories_never_share_a_cache_entry():
    """Both publish v0.11.7/nvim-linux-x86_64.tar.gz; the files differ."""
    official, old = lines.builds(lines.load()["0.11"])
    assert official.url(LINUX.key) != old.url(LINUX.key)
    assert official.sha256[LINUX.key][:16] != old.sha256[LINUX.key][:16]


# --------------------------------------------------------------------------
# fetch_nvim
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


NVIM_TREE = {
    "nvim-linux-x86_64/bin/nvim": b"#!/bin/sh\necho 'NVIM v0.12.6'\n",
    "nvim-linux-x86_64/lib/nvim/parser/c.so": b"so",
    "nvim-linux-x86_64/share/nvim/runtime/init.lua": b"--",
    "nvim-linux-x86_64/share/man/man1/nvim.1": b"not needed at runtime",
}


@NEEDS_TAR_FILTERS
def test_unpack_keeps_only_what_neovim_needs(tmp_path):
    dest = tmp_path / "out"
    fetch_nvim.unpack(_tar(tmp_path / "a.tar.gz", NVIM_TREE), LINUX, dest)
    assert (dest / "bin" / "nvim").is_file()
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


def test_unpack_a_windows_zip(tmp_path):
    archive = tmp_path / "w.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nvim-win64/bin/nvim.exe", b"MZ")
        zf.writestr("nvim-win64/share/nvim/runtime/init.lua", b"--")
    fetch_nvim.unpack(archive, PLATFORMS["windows-x86_64"], tmp_path / "out")
    assert (tmp_path / "out" / "bin" / "nvim.exe").read_bytes() == b"MZ"


def test_an_archive_that_does_not_match_its_pin_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path))

    def evil(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"evil")

    monkeypatch.setattr(fetch_nvim, "download", evil)
    with pytest.raises(SystemExit, match="checksum mismatch"):
        fetch_nvim.archive(lines.find("0.12", LINUX.wheel_tag).build, LINUX)
    assert not list(tmp_path.rglob("*.tar.gz"))


def test_a_verified_cached_archive_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path))
    data = b"archive"
    digest = hashlib.sha256(data).hexdigest()
    build = lines.Build("official", "v0.12.6", {LINUX.key: digest})
    cached = tmp_path / digest[:16] / LINUX.asset
    cached.parent.mkdir()
    cached.write_bytes(data)
    monkeypatch.setattr(fetch_nvim, "download", lambda url, dest: pytest.fail("downloaded"))
    assert fetch_nvim.archive(build, LINUX) == cached


@NEEDS_TAR_FILTERS
def test_fetch_writes_build_json_and_replaces_a_previous_build(tmp_path, monkeypatch):
    dest = tmp_path / "_nvim"
    (dest / "0.12" / "linux-x86_64").mkdir(parents=True)  # a 0.3-era layout
    (dest / "LICENSE.nvim-0.12.txt").write_text("Apache", encoding="utf-8")
    archive = _tar(tmp_path / "a.tar.gz", NVIM_TREE)
    monkeypatch.setattr(fetch_nvim, "archive", lambda build, platform: archive)
    monkeypatch.setattr(fetch_nvim, "runs_here", lambda wheel: False)
    wheel = lines.find("0.12", OLD_X86)
    fetch_nvim.fetch(wheel, dest)
    info = json.loads((dest / "build.json").read_text(encoding="utf-8"))
    assert info == {
        "version": wheel.version,
        "line": "0.12",
        "neovim": wheel.build.version,
        "platform": "linux-x86_64",
        "tag": OLD_X86,
    }
    assert not (dest / "0.12").exists()
    assert (dest / "LICENSE.nvim-0.12.txt").is_file()


@POSIX_ONLY
def test_a_build_that_cannot_run_here_says_why(tmp_path):
    exe = tmp_path / "nvim"
    exe.write_text("#!/bin/sh\necho \"nvim: version 'GLIBC_2.34' not found\" >&2\nexit 1\n")
    exe.chmod(0o755)
    with pytest.raises(SystemExit) as excinfo:
        fetch_nvim.check_runs(exe, "v0.12.6")
    assert "GLIBC_2.34' not found" in str(excinfo.value)
    assert "--wheel" in str(excinfo.value)


# --------------------------------------------------------------------------
# check_wheels
# --------------------------------------------------------------------------
def fake_wheel(tmp_path, wheel, glibc=None, runtime=1600, info_override=None, version=None):
    version = version or wheel.version
    path = tmp_path / f"fast_nvim_release-{version}-py3-none-{wheel.tag}.whl"
    if glibc is None:
        glibc = b"GLIBC_2.17" if "2_17" in wheel.tag else b"GLIBC_2.34"
    info = {
        "version": wheel.version,
        "line": wheel.line,
        "neovim": wheel.build.version,
        "platform": wheel.platform.key,
        "tag": wheel.tag,
        **(info_override or {}),
    }
    with zipfile.ZipFile(path, "w") as zf:
        root = "fast_nvim_release/_nvim/"
        zf.writestr(root + "build.json", json.dumps(info))
        exe = zipfile.ZipInfo(f"{root}bin/{wheel.platform.exe}")
        exe.external_attr = 0o755 << 16
        zf.writestr(exe, b"\x7fELF " + glibc)
        for n in range(runtime):
            zf.writestr(f"{root}share/nvim/runtime/f{n}.vim", b"")
        zf.writestr(f"{root}LICENSE.nvim-{wheel.line}.txt", b"Apache")
        meta = f"fast_nvim_release-{version}.dist-info/"
        tags = "".join(f"Tag: py3-none-{part}\n" for part in wheel.tag.split("."))
        zf.writestr(meta + "WHEEL", f"Root-Is-Purelib: false\n{tags}")
        zf.writestr(meta + "entry_points.txt", "[console_scripts]\n")
    return path


@pytest.mark.parametrize("wheel", lines.wheels(), ids=lambda w: w.label)
def test_every_wheel_in_lines_json_passes_when_complete(tmp_path, wheel):
    assert check_wheels.check(fake_wheel(tmp_path, wheel)) == []


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"glibc": b"GLIBC_2.38"}, "needs glibc (2, 38)"),
        ({"runtime": 10}, "only 10 runtime files"),
        ({"info_override": {"neovim": "v0.12.1"}}, "build.json is"),
        ({"version": "0.12.99"}, "lines.json says"),
    ],
)
def test_a_broken_wheel_fails(tmp_path, kwargs, problem):
    problems = check_wheels.check(
        fake_wheel(tmp_path, lines.find("0.12", LINUX.wheel_tag), **kwargs)
    )
    assert any(problem in item for item in problems), problems


def test_an_old_glibc_wheel_must_not_need_a_newer_glibc(tmp_path):
    wheel = lines.find("0.12", OLD_X86)
    problems = check_wheels.check(fake_wheel(tmp_path, wheel, glibc=b"GLIBC_2.34"))
    assert any("needs glibc (2, 34)" in item for item in problems), problems


# --------------------------------------------------------------------------
# build_wheels: which wheels a run builds
# --------------------------------------------------------------------------
def test_select_by_line_and_tag(monkeypatch):
    monkeypatch.setattr(build_wheels, "host_wheel_tag", lambda: LINUX.wheel_tag)
    chosen = build_wheels.select([], ["host"])
    assert {w.line for w in chosen} == set(lines.load()) and {w.tag for w in chosen} == {
        LINUX.wheel_tag
    }
    assert {w.tag for w in build_wheels.select(["0.12"], [])} == {
        w.tag for w in lines.wheels() if w.line == "0.12"
    }


def test_select_skips_versions_already_published(monkeypatch):
    newest = lines.newest_line()
    monkeypatch.setattr(
        build_wheels, "published", lambda index, version: not version.startswith(newest + ".")
    )
    chosen = build_wheels.select([], [], skip_published="https://test.pypi.org")
    assert chosen and {w.line for w in chosen} == {newest}


# --------------------------------------------------------------------------
# pins: following Neovim's releases
# --------------------------------------------------------------------------
def _release(tag, platform_keys, digests=True):
    assets = []
    for key in platform_keys:
        name = PLATFORMS[key].asset
        digest = hashlib.sha256(f"{tag}{name}".encode()).hexdigest()
        assets.append({"name": name, "digest": f"sha256:{digest}" if digests else None})
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets}


def _upstream(official, old):
    return {
        "official": {t: _release(t, list(PLATFORMS)) for t in official},
        "old_glibc": {t: _release(t, ["linux-x86_64"]) for t in old},
    }


def test_newest_patch_per_line():
    tags = ["v0.12.5", "v0.12.6", "v0.11.7", "nightly", "stable", "v0.12.10"]
    assert pins.newest_patches(tags) == {"0.12": "v0.12.10", "0.11": "v0.11.7"}


def test_update_moves_only_the_lines_that_changed():
    data = copy.deepcopy(lines.load())
    current = {line: spec["official"]["version"] for line, spec in data.items()}
    old = {line: spec["old_glibc"]["version"] for line, spec in data.items() if "old_glibc" in spec}
    upstream = _upstream(
        [*current.values(), "v0.12.99"],  # Neovim ships a 0.12 patch
        [*old.values()],
    )
    changes = pins.plan(data, upstream)
    assert changes == [("0.12", "official", "v0.12.99")]
    before = {line: spec["k"] for line, spec in data.items()}
    assert pins.apply(data, upstream, changes) == ["0.12"]
    assert data["0.12"]["k"] == before["0.12"] + 1
    assert {line: data[line]["k"] for line in data if line != "0.12"} == {
        line: k for line, k in before.items() if line != "0.12"
    }
    assert data["0.12"]["official"]["version"] == "v0.12.99"
    assert set(data["0.12"]["official"]["sha256"]) == set(PLATFORMS)


def test_update_keeps_deliberately_skipped_builds_out():
    data = copy.deepcopy(lines.load())
    upstream = _upstream(["v0.10.99"], [])
    pins.apply(data, upstream, [("0.10", "official", "v0.10.99")])
    assert "linux-arm64" not in data["0.10"]["official"]["sha256"]
    assert "linux-arm64" in data["0.10"]["official"]["skip"]


def test_update_adds_a_new_release_line():
    data = copy.deepcopy(lines.load())
    upstream = _upstream(["v0.99.0"], [])
    changes = [c for c in pins.plan(data, upstream) if c[0] == "0.99"]
    assert changes == [("0.99", "official", "v0.99.0")]
    pins.apply(data, upstream, changes)
    assert data["0.99"]["k"] == 1


def test_a_release_without_published_checksums_stops_the_update(monkeypatch):
    data = copy.deepcopy(lines.load())
    upstream = {"official": {"v0.12.99": _release("v0.12.99", list(PLATFORMS), digests=False)}}
    with pytest.raises(SystemExit, match="no published checksum"):
        pins.apply(data, upstream, [("0.12", "official", "v0.12.99")])


def test_bump_all(tmp_path):
    path = tmp_path / "lines.json"
    lines.save(lines.load(), path)
    before = {line: spec["k"] for line, spec in lines.load(path).items()}
    assert pins.main(["--file", str(path), "bump", "--all"]) == 0
    assert {line: spec["k"] for line, spec in lines.load(path).items()} == {
        line: k + 1 for line, k in before.items()
    }


def test_lagging_old_glibc_builds_are_a_note():
    data = copy.deepcopy(lines.load())
    data["0.12"]["old_glibc"]["version"] = "v0.12.1"
    assert any(note.startswith("0.12:") for note in pins.lagging(data))


# --------------------------------------------------------------------------
# release notes
# --------------------------------------------------------------------------
def test_release_notes_list_each_wheels_neovim(tmp_path):
    official = fake_wheel(tmp_path, lines.find("0.12", LINUX.wheel_tag))
    old = fake_wheel(tmp_path, lines.find("0.12", OLD_X86))
    notes = release_notes.notes([official, old])
    (version,) = notes
    assert f'pip install "fast-nvim-release=={version}"' in notes[version]
    assert LINUX.wheel_tag in notes[version] and OLD_X86 in notes[version]
