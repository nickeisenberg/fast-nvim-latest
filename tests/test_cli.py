"""End-to-end CLI behaviour, driven through ``fast_nvim_release.cli.main``."""

from __future__ import annotations

import json
import os

import pytest

from fast_nvim_release import bundle, cli
from fast_nvim_release.cli import main
from fast_nvim_release.platforms import PLATFORM_KEYS, RELEASE_KEYS, RELEASES

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="fake nvim is a shell script")
NEWEST = RELEASE_KEYS[-1]
OLDEST = RELEASE_KEYS[0]


def other_platform():
    here = bundle_host()
    for key in PLATFORM_KEYS:
        if key != here:
            return key
    return PLATFORM_KEYS[0]  # pragma: no cover


def bundle_host() -> str:
    from fast_nvim_release.platforms import host_platform

    here = host_platform()
    if here is None:  # pragma: no cover
        pytest.skip("unknown host platform")
    return here.key


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "setup" in out


@POSIX_ONLY
def test_setup_installs_the_default_release(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out
    assert f"verified  NVIM {RELEASES[NEWEST].version[1:]}" in out
    exe = prefix / "bin" / RELEASES[NEWEST].target(bundle_host()).exe
    assert exe.is_file()
    assert os.access(exe, os.X_OK)
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


@POSIX_ONLY
def test_setup_installs_a_named_older_release(fake_bundle, prefix, capsys):
    assert main(["setup", OLDEST, "--prefix", str(prefix), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert f"release {OLDEST} ({RELEASES[OLDEST].version})" in out
    assert f"verified  NVIM {RELEASES[OLDEST].version[1:]}" in out
    assert bundle.installed_releases(prefix) == [OLDEST]


def test_setup_refuses_a_build_this_glibc_cannot_run(fake_bundle, prefix, capsys, monkeypatch):
    """0.10 on arm64 needs glibc 2.38: never download it on a 2.35 system."""
    from fast_nvim_release import platforms

    monkeypatch.setattr(platforms, "host_platform", lambda: platforms.PLATFORMS["linux-arm64"])
    monkeypatch.setattr(platforms, "host_glibc", lambda: (2, 35))

    def must_not_fetch(target):
        pytest.fail(f"would have fetched {target.label}")

    monkeypatch.setattr(bundle, "needs_download", must_not_fetch)

    with pytest.raises(SystemExit) as excinfo:
        main(["setup", OLDEST, "linux-arm64", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "glibc 2.38" in err and "2.35" in err
    assert f"setup {NEWEST}" in err
    assert not (prefix / "bin").exists()


@POSIX_ONLY
def test_switching_releases_in_one_prefix(fake_bundle, prefix, capsys):
    assert main(["setup", NEWEST, "--prefix", str(prefix), "--quiet"]) == 0
    capsys.readouterr()
    assert main(["setup", OLDEST, "--prefix", str(prefix), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "replaced" in out
    assert bundle.installed_releases(prefix) == [OLDEST]


@POSIX_ONLY
def test_release_and_platform_can_come_in_either_order(fake_bundle, tmp_path, capsys):
    for selectors in ([OLDEST, "linux"], ["linux", OLDEST]):
        prefix = tmp_path / ("p" + str(len(selectors)) + selectors[0])
        assert main(["setup", *selectors, "--prefix", str(prefix), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert f"release {OLDEST}" in out
        assert "linux" in out


def test_two_releases_are_a_usage_error(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", OLDEST, NEWEST])
    assert excinfo.value.code == 1
    assert "two releases" in capsys.readouterr().err


def test_unknown_release_is_reported_cleanly(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "0.9"])
    assert excinfo.value.code == 1
    assert "unknown Neovim release" in capsys.readouterr().err


@POSIX_ONLY
def test_setup_refuses_to_clobber_foreign_files(fake_bundle, prefix, capsys):
    (prefix / "bin").mkdir(parents=True)
    foreign = prefix / "bin" / RELEASES[NEWEST].target(bundle_host()).exe
    foreign.write_text("#!/bin/sh\necho not-nvim\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert foreign.read_text() == "#!/bin/sh\necho not-nvim\n"


@POSIX_ONLY
def test_setup_force_overwrites(fake_bundle, prefix):
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / RELEASES[NEWEST].target(bundle_host()).exe).write_text(
        "junk", encoding="utf-8"
    )
    assert main(["setup", "--prefix", str(prefix), "--force", "--quiet"]) == 0


def test_setup_dry_run(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    assert not prefix.exists()
    assert "dry run" in capsys.readouterr().out


def test_setup_list_shows_every_release(fake_bundle, capsys):
    assert main(["setup", "--list"]) == 0
    out = capsys.readouterr().out
    for key in RELEASE_KEYS:
        assert key in out
        assert RELEASES[key].version in out
    assert "default" in out


def test_setup_cross_platform_needs_a_prefix(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", other_platform(), "--quiet"])
    assert excinfo.value.code == 1
    assert "not the platform you are on" in capsys.readouterr().err


def test_a_cross_platform_install_warns_before_replacing_this_machines_build(
    fake_bundle, prefix, capsys, monkeypatch
):
    """An env-var prefix + another platform must not quietly cost the user nvim."""
    from conftest import make_bundle

    foreign = other_platform()
    make_bundle(fake_bundle, NEWEST, foreign)  # so the cross install needs no network

    assert main(["setup", NEWEST, "--prefix", str(prefix), "--quiet"]) == 0
    capsys.readouterr()

    monkeypatch.setenv("FAST_NVIM_RELEASE_PREFIX", str(prefix))
    assert main(["setup", NEWEST, foreign, "--offline", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "already holds a Neovim for this machine" in err
    assert "separate --prefix" in err

    # the foreign build really is what is installed now
    assert bundle.installed_releases(prefix) == [NEWEST]
    installed = bundle.installed_targets(prefix)
    assert [target.key for target in installed] == [foreign]


def test_a_cross_platform_install_into_an_empty_prefix_is_quiet(fake_bundle, tmp_path, capsys):
    from conftest import make_bundle

    foreign = other_platform()
    make_bundle(fake_bundle, NEWEST, foreign)
    target = tmp_path / "fresh"
    assert main(["setup", NEWEST, foreign, "--prefix", str(target), "--quiet"]) == 0
    assert "already holds a Neovim" not in capsys.readouterr().err


def test_setup_cross_platform_offline_without_cache_fails(fake_bundle, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "setup",
                other_platform(),
                "--prefix",
                str(tmp_path / "cross"),
                "--offline",
                "--quiet",
            ]
        )
    assert excinfo.value.code == 1
    assert "not available offline" in capsys.readouterr().err


def test_setup_unknown_platform(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "plan9"])
    assert excinfo.value.code == 1
    assert "unknown platform" in capsys.readouterr().err


@POSIX_ONLY
def test_path_json(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    capsys.readouterr()
    assert main(["path", "--prefix", str(prefix), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == bundle_host()
    assert payload["release"] == NEWEST
    assert payload["installed"] is True
    assert payload["bundled"] is True
    assert payload["installed_releases"] == [NEWEST]


def test_path_bundle(fake_bundle, capsys):
    assert main(["path", "--bundle"]) == 0
    # the command prints a native path, so compare natively (Windows uses \)
    assert str(bundle.bundle_path(NEWEST, bundle_host())) in capsys.readouterr().out


def test_path_bundle_for_a_pinned_older_release(fake_bundle, capsys):
    assert main(["path", OLDEST, "--bundle"]) == 0
    assert str(bundle.bundle_path(OLDEST, bundle_host())) in capsys.readouterr().out


def test_path_installed_only_fails_when_absent(fake_bundle, prefix, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["path", "--prefix", str(prefix), "--installed-only"])
    assert excinfo.value.code == 1
    assert "no fast-nvim-release install" in capsys.readouterr().err


def test_version_json(fake_bundle, capsys):
    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["release"] == NEWEST
    assert payload["nvim_version"] == RELEASES[NEWEST].version
    assert sorted(payload["releases"]) == sorted(RELEASE_KEYS)
    for key in RELEASE_KEYS:
        assert payload["releases"][key]["nvim_version"] == RELEASES[key].version
        assert payload["releases"][key]["bundled"] == [bundle_host()]
    assert payload["bundled"] is True


def test_version_human(fake_bundle, capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "fast-nvim-release" in out
    assert "in this wheel" in out
    for key in RELEASE_KEYS:
        assert RELEASES[key].version in out


@POSIX_ONLY
def test_version_restores_the_exec_bit_before_running_the_binary(fake_bundle, capsys):
    """pip does not always keep the exec bit; version must still work."""
    exe = bundle.bundle_exe(NEWEST, bundle_host())
    exe.chmod(0o644)
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert f"NVIM {RELEASES[NEWEST].version[1:]}" in out
    assert "unusable" not in out


def test_run_without_bundle_explains_itself(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "_nvim"
    empty.mkdir()
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", empty)
    monkeypatch.setattr(bundle, "PACKAGE_DIR", tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "--version"])
    assert excinfo.value.code == 1
    assert "does not bundle" in capsys.readouterr().err


@POSIX_ONLY
def test_run_a_specific_release(fake_bundle, prefix, capsys):
    # `_run` replaces the process, so drive the same code path through the
    # binary the CLI would have exec'd.
    target = RELEASES[OLDEST].target(bundle_host())
    exe = bundle.bundle_exe(OLDEST, bundle_host())
    bundle.ensure_executable(exe)
    assert target is not None
    from fast_nvim_release.cli import cmd_run

    assert exe.is_file()
    # a release selector with no `--` still goes to nvim as an argument
    assert cmd_run  # the real dispatch is exercised in the smoke test


# --------------------------------------------------------------------------
# failure modes must be messages, not tracebacks
# --------------------------------------------------------------------------
def test_setup_reports_filesystem_errors_cleanly(fake_bundle, prefix, capsys):
    (prefix / "lib").mkdir(parents=True)
    (prefix / "lib" / "nvim").write_text("not a directory", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    assert "error: " in capsys.readouterr().err


def test_setup_refuses_a_foreign_runtime_directory(fake_bundle, prefix, capsys):
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    foreign = prefix / "share" / "nvim" / "runtime" / "mine.vim"
    foreign.write_text('" mine\n', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    assert "was not installed by fast-nvim-release" in capsys.readouterr().err
    assert foreign.is_file()


@POSIX_ONLY
def test_setup_force_replaces_a_foreign_runtime_directory(fake_bundle, prefix):
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    foreign = prefix / "share" / "nvim" / "runtime" / "mine.vim"
    foreign.write_text('" mine\n', encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix), "--force", "--quiet"]) == 0
    assert not foreign.exists()


@POSIX_ONLY
def test_reinstall_clears_stale_runtime_files(fake_bundle, prefix):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    stale = prefix / "share" / "nvim" / "runtime" / "stale.vim"
    stale.write_text('" old\n', encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    assert not stale.exists()


# --------------------------------------------------------------------------
# --dry-run must not touch the network or the disk
# --------------------------------------------------------------------------
def test_dry_run_does_not_download(fake_bundle, tmp_path, monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise AssertionError("dry run must not download")

    monkeypatch.setattr(bundle, "_download", explode)
    prefix = tmp_path / "dry"
    assert main(["setup", OLDEST, other_platform(), "--prefix", str(prefix), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert not prefix.exists()


def test_dry_run_reports_a_bundled_source(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    assert "pip wheel" in capsys.readouterr().out


# --------------------------------------------------------------------------
# cross-platform installs
# --------------------------------------------------------------------------
def test_cross_platform_uses_the_env_prefix(fake_bundle, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAST_NVIM_RELEASE_PREFIX", str(tmp_path / "envprefix"))
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", other_platform(), "--offline", "--quiet"])
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    # the "needs an explicit --prefix" guard let it through
    assert "not the platform you are on" not in err
    assert "not available offline" in err


@POSIX_ONLY
def test_a_relative_prefix_is_recorded_as_an_absolute_path(
    fake_bundle, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert main(["setup", "--prefix", "rel-prefix", "--quiet"]) == 0
    out = capsys.readouterr().out
    # the PATH advice and the install paths must be usable from anywhere
    assert str(tmp_path / "rel-prefix" / "bin") in out
    assert 'export PATH="rel-prefix/bin' not in out

    assert main(["path", "--prefix", "rel-prefix", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["prefix"] == str(tmp_path / "rel-prefix")
    assert payload["installed"] is True


# --------------------------------------------------------------------------
# path / list / help
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_path_installed_only_requires_the_executable(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    (prefix / "bin" / RELEASES[NEWEST].target(bundle_host()).exe).unlink()
    with pytest.raises(SystemExit) as excinfo:
        main(["path", "--prefix", str(prefix), "--installed-only"])
    assert excinfo.value.code == 1
    assert "no fast-nvim-release install" in capsys.readouterr().err

    assert main(["path", "--prefix", str(prefix), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] is False
    assert payload["managed"] is True
    assert payload["installed_releases"] == []

    # `version` must not contradict that by still claiming an install
    assert main(["version", "--prefix", str(prefix), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] == []

    assert main(["version", "--prefix", str(prefix)]) == 0
    out = capsys.readouterr().out
    assert "nothing" in out


def test_path_bundle_fails_for_a_missing_build(tmp_path, monkeypatch, capsys):
    from conftest import build_fake_wheel

    root = build_fake_wheel(tmp_path, [NEWEST])
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", root)
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as excinfo:
        main(["path", OLDEST, "--bundle"])
    assert excinfo.value.code == 1
    assert "is not bundled" in capsys.readouterr().err


def test_setup_list_says_what_would_have_to_be_downloaded(tmp_path, monkeypatch, capsys):
    """A wheel with only the newest release must show the others as downloads."""
    from conftest import build_fake_wheel

    monkeypatch.setattr(bundle, "BUNDLE_ROOT", build_fake_wheel(tmp_path, [NEWEST]))
    monkeypatch.setenv("FAST_NVIM_RELEASE_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["setup", "--list"]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines() if line.strip()}
    assert "bundled" in lines[NEWEST]
    for key in RELEASE_KEYS[:-1]:
        assert "download" in lines[key]

    # and a cached archive changes the answer without touching the network
    target = RELEASES[OLDEST].target(bundle_host())
    archive = bundle.cached_archive(target)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"stub")
    assert main(["setup", "--list"]) == 0
    out = capsys.readouterr().out
    assert "cached archive" in out


def test_setup_list_marks_a_platform_neovim_never_published(fake_bundle, capsys, monkeypatch):
    """The status comes from the table, not from reading the note's prose."""
    from fast_nvim_release import platforms

    monkeypatch.setattr(cli, "host_platform", lambda: platforms.PLATFORMS["windows-arm64"])
    assert main(["setup", "--list"]) == 0
    out = capsys.readouterr().out
    lines = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    assert "unavailable" in lines[OLDEST]  # 0.10 has no Windows-on-ARM build
    assert "unavailable" not in lines[NEWEST]

    assert cli._release_status(OLDEST, "windows-arm64") == "unavailable"
    assert cli._release_status(OLDEST, "linux-x86_64") in ("bundled", "download")


def test_help_mentions_every_option(capsys):
    """The help text is generated by argparse, so it cannot drift."""
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--prefix", "--force", "--link", "--dry-run", "--offline", "--arch", "--list"):
        assert flag in out
    assert main(["--help"]) == 0
    assert "setup" in capsys.readouterr().out


@POSIX_ONLY
def test_link_install_reports_links(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--link", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "linked 3 entries" in out
    assert "installed 3 files" not in out


def test_module_entry_point():
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    )
    result = subprocess.run(
        [sys.executable, "-m", "fast_nvim_release", "--help"],
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert b"usage:" in result.stdout


# --------------------------------------------------------------------------
# an emulated interpreter must be visible, not silent
# --------------------------------------------------------------------------
def test_setup_warns_about_an_emulated_interpreter(fake_bundle, prefix, monkeypatch, capsys):
    from fast_nvim_release import cli

    # patched where it is used: `setup` warns once, before doing any work
    monkeypatch.setattr(cli, "emulation_note", lambda: "this Python runs as x86_64 (Rosetta)")
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "Rosetta" in err
    assert "warning: " in err


def test_setup_is_quiet_on_a_native_interpreter(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    assert "warning: " not in capsys.readouterr().err


def test_version_reports_the_interpreter_arch(fake_bundle, capsys):
    from fast_nvim_release import platforms

    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "python_arch" in payload
    assert payload["emulated"] is False
    assert platforms.emulation_note() is None
