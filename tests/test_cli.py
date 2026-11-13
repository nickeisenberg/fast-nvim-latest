"""End-to-end CLI behaviour, driven through ``fast_nvim.cli.main``."""

from __future__ import annotations

import json
import os

import pytest

from fast_nvim import bundle
from fast_nvim.cli import main
from fast_nvim.platforms import TARGETS, host_target

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


def test_setup_cross_platform_offline_without_cache_fails(
    fake_bundle, tmp_path, capsys
):
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
    assert bundle.bundle_path(host_target().key).as_posix() in capsys.readouterr().out


def test_path_installed_only_fails_when_absent(fake_bundle, prefix, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["path", "--prefix", str(prefix), "--installed-only"])
    assert excinfo.value.code == 1
    assert "no fast-nvim install" in capsys.readouterr().err


def test_version_json(fake_bundle, capsys):
    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["vendored_targets"] == [host_target().key]
    assert payload["build_info"]["nvim_version"] == "v9.9.9"


def test_version_human(fake_bundle, capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "fast-nvim" in out
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


def test_module_entry_point():
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    )
    result = subprocess.run(
        [sys.executable, "-m", "fast_nvim", "--help"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert result.returncode == 0
    assert b"usage:" in result.stdout
