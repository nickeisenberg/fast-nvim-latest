"""End-to-end CLI behaviour, driven through ``fast_nvim_latest.cli.main``."""

from __future__ import annotations

import json
import os

import pytest

from fast_nvim_latest import bundle
from fast_nvim_latest.cli import main
from fast_nvim_latest.platforms import TARGETS, host_target

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="fake nvim is a shell script")


def other_target():
    host = host_target()
    for key, target in TARGETS.items():
        if host is None or key != host.key:
            return target
    return next(iter(TARGETS.values()))


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "setup" in out


@POSIX_ONLY
def test_setup_installs_bundled_nvim(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out
    assert "verified  NVIM v9.9.9" in out
    exe = prefix / "bin" / host_target().exe
    assert exe.is_file()
    assert os.access(exe, os.X_OK)
    assert (prefix / "share" / "nvim" / "runtime" / "init.lua").is_file()


@POSIX_ONLY
def test_setup_defaults_to_this_platform(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert host_target().key in out
    assert "this platform" not in out


@POSIX_ONLY
def test_setup_refuses_to_clobber_foreign_files(fake_bundle, prefix, capsys):
    (prefix / "bin").mkdir(parents=True)
    foreign = prefix / "bin" / host_target().exe
    foreign.write_text("#!/bin/sh\necho not-nvim\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert foreign.read_text() == "#!/bin/sh\necho not-nvim\n"


@POSIX_ONLY
def test_setup_force_overwrites(fake_bundle, prefix):
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / host_target().exe).write_text("junk", encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix), "--force", "--quiet"]) == 0
    assert "fake nvim" in (prefix / "bin" / host_target().exe).read_text()


def test_setup_dry_run(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    assert not prefix.exists()
    assert "dry run" in capsys.readouterr().out


def test_setup_list(fake_bundle, capsys):
    assert main(["setup", "--list"]) == 0
    out = capsys.readouterr().out
    for key in TARGETS:
        assert key in out


def test_setup_cross_platform_needs_a_prefix(fake_bundle, capsys):
    target = other_target()
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", target.key, "--quiet"])
    assert excinfo.value.code == 1
    assert "not the platform you are on" in capsys.readouterr().err


def test_setup_cross_platform_offline_without_cache_fails(fake_bundle, tmp_path, capsys):
    target = other_target()
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "setup",
                target.key,
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
    assert payload["target"] == host_target().key
    assert payload["installed"] is True
    assert payload["bundled"] is True
    assert payload["exe"].endswith(host_target().exe)


def test_path_bundle(fake_bundle, capsys):
    assert main(["path", "--bundle"]) == 0
    # the command prints a native path, so compare natively (Windows uses \)
    assert str(bundle.bundle_path(host_target().key)) in capsys.readouterr().out


def test_path_installed_only_fails_when_absent(fake_bundle, prefix, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["path", "--prefix", str(prefix), "--installed-only"])
    assert excinfo.value.code == 1
    assert "no fast-nvim-latest install" in capsys.readouterr().err


def test_version_json(fake_bundle, capsys):
    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["vendored_targets"] == [host_target().key]
    assert payload["build_info"]["nvim_version"] == "v9.9.9"


def test_version_human(fake_bundle, capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "fast-nvim-latest" in out
    assert "in this wheel" in out


def test_run_without_bundle_explains_itself(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "_nvim"
    empty.mkdir()
    monkeypatch.setattr(bundle, "BUNDLE_ROOT", empty)
    monkeypatch.setattr(bundle, "PACKAGE_DIR", tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "--version"])
    assert excinfo.value.code == 1
    assert "no Neovim binary" in capsys.readouterr().err


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
    assert "was not installed by fast-nvim-latest" in capsys.readouterr().err
    assert foreign.is_file()


@POSIX_ONLY
def test_setup_force_replaces_a_foreign_runtime_directory(fake_bundle, prefix):
    (prefix / "share" / "nvim" / "runtime").mkdir(parents=True)
    foreign = prefix / "share" / "nvim" / "runtime" / "mine.vim"
    foreign.write_text('" mine\n', encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix), "--force", "--quiet"]) == 0
    assert not foreign.exists()


def test_a_prefix_holds_one_build(fake_bundle, prefix, capsys):
    (prefix / "share" / "fast-nvim-latest").mkdir(parents=True)
    other = next(key for key in TARGETS if key != host_target().key)
    bundle.marker_file(prefix, other).write_text("{}", encoding="utf-8")
    (prefix / "lib" / "nvim").mkdir(parents=True)
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--prefix", str(prefix), "--quiet"])
    assert excinfo.value.code == 1
    assert f"install for {other}" in capsys.readouterr().err


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
    target = other_target()

    def explode(*args, **kwargs):
        raise AssertionError("dry run must not download")

    monkeypatch.setattr(bundle, "_download", explode)
    prefix = tmp_path / "dry"
    assert main(["setup", target.key, "--prefix", str(prefix), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "not fetched yet" in out
    assert not prefix.exists()
    assert not bundle.cached_archive(target).exists()


def test_dry_run_reports_a_bundled_source(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--dry-run"]) == 0
    assert "pip wheel" in capsys.readouterr().out


# --------------------------------------------------------------------------
# cross-platform installs
# --------------------------------------------------------------------------
def test_cross_platform_uses_FAST_NVIM_LATEST_PREFIX(fake_bundle, tmp_path, monkeypatch, capsys):
    target = other_target()
    monkeypatch.setenv("FAST_NVIM_LATEST_PREFIX", str(tmp_path / "envprefix"))
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", target.key, "--offline", "--quiet"])
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    # the "needs an explicit --prefix" guard let it through
    assert "not the platform you are on" not in err
    assert "not available offline" in err


# --------------------------------------------------------------------------
# path / list / help
# --------------------------------------------------------------------------
@POSIX_ONLY
def test_path_installed_only_requires_the_executable(fake_bundle, prefix, capsys):
    assert main(["setup", "--prefix", str(prefix), "--quiet"]) == 0
    (prefix / "bin" / host_target().exe).unlink()
    with pytest.raises(SystemExit) as excinfo:
        main(["path", "--prefix", str(prefix), "--installed-only"])
    assert excinfo.value.code == 1
    assert "no fast-nvim-latest install" in capsys.readouterr().err

    assert main(["path", "--prefix", str(prefix), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] is False
    assert payload["managed"] is True


def test_path_bundle_fails_for_a_missing_build(fake_bundle, capsys):
    target = other_target()
    with pytest.raises(SystemExit) as excinfo:
        main(["path", target.key, "--bundle"])
    assert excinfo.value.code == 1
    assert "not bundled in this wheel" in capsys.readouterr().err


def test_setup_list_knows_about_cached_archives(fake_bundle, capsys):
    target = other_target()
    archive = bundle.cached_archive(target)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"stub")
    assert main(["setup", "--list"]) == 0
    lines = {
        line.split()[0]: line
        for line in capsys.readouterr().out.splitlines()
        if line.strip() and not line.startswith(" ")
    }
    assert "cached" in lines[target.key]
    assert "bundled" in lines[host_target().key]


def test_help_mentions_every_option(capsys):
    """The help text is generated by argparse, so it cannot drift."""
    with pytest.raises(SystemExit) as excinfo:
        main(["setup", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--prefix", "--force", "--link", "--dry-run", "--offline", "--arch"):
        assert flag in out
    assert main(["--help"]) == 0
    assert "setup" in capsys.readouterr().out


def test_run_help_flags_are_documented(capsys):
    assert main([]) == 0
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
        [sys.executable, "-m", "fast_nvim_latest", "--help"],
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
    from fast_nvim_latest import cli

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
    from fast_nvim_latest import platforms

    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "python_arch" in payload
    assert payload["emulated"] is False
    assert platforms.emulation_note() is None
