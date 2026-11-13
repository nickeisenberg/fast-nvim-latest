"""Command line interface: ``fast-nvim-release setup | path | run``."""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__, bundle
from .bundle import FastNvimError
from .platforms import DEFAULT_RELEASE, RELEASES, Platform, host_platform, parse_release

PROG = "fast-nvim-release"


def _host() -> Platform:
    here = host_platform()
    if here is None:
        raise FastNvimError(f"there is no Neovim build for this platform ({sys.platform})")
    return here


def _release(spec: Optional[str]) -> str:
    try:
        return parse_release(spec)
    except ValueError as exc:
        raise FastNvimError(str(exc)) from None


def _version_text() -> str:
    here = host_platform()
    if here is None:
        return f"{PROG} {__version__}"
    builds = ", ".join(bundle.bundled_version(k, here) for k in bundle.bundled_releases(here))
    return f"{PROG} {__version__} (Neovim {builds or 'none'} for {here.key})"


def _on_path(directory: Path) -> bool:
    wanted = os.path.normcase(str(directory))
    return any(
        entry and os.path.normcase(os.path.abspath(entry)) == wanted
        for entry in os.environ.get("PATH", "").split(os.pathsep)
    )


def cmd_setup(args: argparse.Namespace) -> int:
    release = _release(args.release)
    prefix = bundle.normalize(args.prefix)
    here = _host()
    exe = bundle.install(release, here, prefix, force=args.force)
    bundle.verify(exe, prefix)
    print(f"installed Neovim {bundle.bundled_version(release, here)} into {prefix}")
    print(f"  {exe}")
    if not _on_path(exe.parent):
        if os.name == "nt":
            hint = "add it to PATH in System Properties > Environment Variables"
        else:
            hint = f'add it to PATH, e.g. in ~/.bashrc: export PATH="{exe.parent}:$PATH"'
        print(f"note: {exe.parent} is not on your PATH; {hint}", file=sys.stderr)
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    prefix = bundle.normalize(args.prefix) if args.prefix else bundle.default_prefix()
    exe = prefix / "bin" / ("nvim.exe" if os.name == "nt" else "nvim")
    if not bundle.installed(prefix) or not exe.is_file():
        raise FastNvimError(f"{PROG} has not installed Neovim in {prefix}")
    print(exe)
    return 0


def cmd_run(argv: Sequence[str]) -> int:
    """``run [RELEASE] [--] [nvim args...]``: start a bundled Neovim directly."""
    args = list(argv)
    release = DEFAULT_RELEASE
    # only a version (0.10, v0.11, 12) selects a release; anything else,
    # including words like `new` or `latest`, is passed to Neovim
    if args and re.match(r"v?\d", args[0]):
        try:
            release = parse_release(args[0])
            args = args[1:]
        except ValueError:
            pass
    if args[:1] == ["--"]:
        args = args[1:]
    exe = str(bundle.bundled_exe(release, _host()))
    if os.name == "nt":
        # Ctrl-C reaches every process on the console; Neovim handles it and
        # this launcher must not die (or kill the editor) because of it
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return subprocess.call([exe, *args])
        finally:
            signal.signal(signal.SIGINT, previous)
    os.execv(exe, [exe, *args])  # replaces this process
    return 0  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Install the Neovim releases bundled in this wheel.",
        epilog=f"example: {PROG} setup 0.12 --prefix ~/.local",
    )
    parser.add_argument("--version", action="version", version=_version_text())
    commands = parser.add_subparsers(metavar="COMMAND")

    releases = ", ".join(RELEASES)
    setup = commands.add_parser("setup", help="install a release into a prefix")
    setup.add_argument("release", nargs="?", help=f"{releases} (default: {DEFAULT_RELEASE})")
    setup.add_argument("--prefix", required=True, help="install prefix, e.g. ~/.local")
    setup.add_argument("--force", action="store_true", help="overwrite files not installed here")
    setup.set_defaults(func=cmd_setup)

    path = commands.add_parser("path", help="print the nvim this tool installed in a prefix")
    path.add_argument("--prefix", help=f"prefix to look in (default: {bundle.default_prefix()})")
    path.set_defaults(func=cmd_path)

    # `main` handles `run` itself so that nvim's own flags reach nvim; this
    # parser only puts the command into --help
    run = commands.add_parser("run", help="run a bundled nvim without installing it")
    run.add_argument("args", nargs=argparse.REMAINDER, help="[RELEASE] [--] nvim arguments")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args: List[str] = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["run"]:
            return cmd_run(args[1:])
        parser = build_parser()
        parsed = parser.parse_args(args)
        if not hasattr(parsed, "func"):
            parser.print_help()
            return 0
        return parsed.func(parsed)
    except (FastNvimError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
