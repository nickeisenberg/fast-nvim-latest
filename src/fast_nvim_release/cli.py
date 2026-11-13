"""Command line interface: ``fast-nvim-release setup | path | run``.

This is the stable interface.  The package version (``0.12.3``) names the
Neovim line, so it cannot announce breaking changes: commands and flags are
only ever added, never removed or changed in meaning.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__, bundle
from .bundle import FastNvimError

PROG = "fast-nvim-release"


def _version_text() -> str:
    info = bundle.build_info()
    if not info:
        return f"{PROG} {__version__} (no Neovim build bundled)"
    return f"{PROG} {__version__} (Neovim {info.get('neovim')} for {info.get('platform')})"


def _on_path(directory: Path) -> bool:
    wanted = os.path.normcase(str(directory))
    return any(
        entry and os.path.normcase(os.path.abspath(entry)) == wanted
        for entry in os.environ.get("PATH", "").split(os.pathsep)
    )


def cmd_setup(args: argparse.Namespace) -> int:
    prefix = bundle.normalize(args.prefix)
    exe = bundle.install(prefix, force=args.force)
    bundle.verify(exe, prefix)
    print(f"installed Neovim {bundle.build_info().get('neovim', '')} into {prefix}".rstrip())
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
    """``run [--] [nvim args...]``: start the bundled Neovim directly."""
    args = list(argv)
    if args[:1] == ["--"]:
        args = args[1:]
    exe = str(bundle.bundled_exe())
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
        description="Install the Neovim bundled in this package. Each package version "
        'carries one Neovim line: pip install "fast-nvim-release==0.11.*" picks it.',
        epilog=f"example: {PROG} setup --prefix ~/.local",
    )
    parser.add_argument("--version", action="version", version=_version_text())
    commands = parser.add_subparsers(metavar="COMMAND")

    setup = commands.add_parser("setup", help="install the bundled Neovim into a prefix")
    setup.add_argument("--prefix", required=True, help="install prefix, e.g. ~/.local")
    setup.add_argument("--force", action="store_true", help="overwrite files not installed here")
    setup.set_defaults(func=cmd_setup)

    path = commands.add_parser("path", help="print the nvim this tool installed in a prefix")
    path.add_argument("--prefix", help=f"prefix to look in (default: {bundle.default_prefix()})")
    path.set_defaults(func=cmd_path)

    # `main` handles `run` itself so that nvim's own flags reach nvim; this
    # parser only puts the command into --help
    run = commands.add_parser("run", help="run the bundled nvim without installing it")
    run.add_argument("args", nargs=argparse.REMAINDER, help="[--] arguments for nvim")
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
