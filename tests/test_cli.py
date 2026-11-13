"""The command line, driven through ``main``."""

from __future__ import annotations

import pytest
from conftest import POSIX_ONLY

from fast_nvim_release import __version__, bundle, cli
from fast_nvim_release.cli import main
from fast_nvim_release.platforms import RELEASES


def test_no_arguments_prints_help(capsys):
    assert main([]) == 0
    assert "setup" in capsys.readouterr().out


def test_version_lists_the_bundled_releases(fake_bundle, host, capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    out = capsys.readouterr().out
    assert f"fast-nvim-release {__version__}" in out
    assert "v0.12.6" in out
    assert host.key in out


@POSIX_ONLY
def test_setup_installs_and_verifies(fake_bundle, host, prefix, capsys):
    assert main(["setup", "0.11", "--prefix", str(prefix)]) == 0
    out = capsys.readouterr().out
    assert f"installed Neovim v0.11.7 into {prefix}" in out
    assert bundle.installed(prefix)["release"] == "0.11"


def test_setup_needs_a_prefix(fake_bundle, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["setup"])
    assert excinfo.value.code == 2
    assert "--prefix" in capsys.readouterr().err


def test_errors_are_one_line_messages(fake_bundle, prefix, capsys):
    assert main(["setup", "0.9", "--prefix", str(prefix)]) == 1
    assert capsys.readouterr().err.startswith("error: unknown Neovim release '0.9'")


def test_setup_refuses_foreign_files_without_force(fake_bundle, host, prefix, capsys):
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / host.exe).write_text("mine", encoding="utf-8")
    assert main(["setup", "--prefix", str(prefix)]) == 1
    assert "--force" in capsys.readouterr().err


def test_path(fake_bundle, host, prefix, capsys):
    assert main(["path", "--prefix", str(prefix)]) == 1
    assert "has not installed Neovim" in capsys.readouterr().err
    bundle.install("0.12", host, prefix)
    assert main(["path", "--prefix", str(prefix)]) == 0
    assert capsys.readouterr().out.strip() == str(prefix / "bin" / host.exe)


@pytest.fixture()
def launched(monkeypatch):
    """Capture what `run` would start instead of starting it."""
    calls = []

    def fake_exec(path, argv):
        calls.append((path, list(argv[1:])))
        return 0

    monkeypatch.setattr(cli.os, "execv", fake_exec)
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: fake_exec(argv[0], argv))
    return calls


@pytest.mark.parametrize(
    "argv, release, passed",
    [
        ([], "0.12", []),
        (["file.txt"], "0.12", ["file.txt"]),
        (["0.11", "file.txt"], "0.11", ["file.txt"]),
        (["v0.11", "--", "--version"], "0.11", ["--version"]),
        (["--", "0.11"], "0.12", ["0.11"]),
        (["new"], "0.12", ["new"]),
        (["latest"], "0.12", ["latest"]),
        (["2024-notes.txt"], "0.12", ["2024-notes.txt"]),
    ],
)
def test_run(fake_bundle, host, launched, argv, release, passed):
    main(["run", *argv])
    path, args = launched[0]
    assert path == str(bundle.bundled_exe(release, host))
    assert args == passed


def test_releases_in_help_match_the_table(capsys):
    with pytest.raises(SystemExit):
        main(["setup", "--help"])
    assert ", ".join(RELEASES) in capsys.readouterr().out
