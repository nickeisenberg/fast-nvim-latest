"""Command line interface: ``fast-nvim <command>``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

from . import __version__
from . import bundle
from .platforms import (
    NVIM_VERSION,
    TARGET_KEYS,
    TARGETS,
    Target,
    host_target,
    parse_target,
)
from .bundle import FastNvimError

PROG = "fast-nvim"


# --------------------------------------------------------------------------
# tiny output helpers (no third party dependencies)
# --------------------------------------------------------------------------
def _colors_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _paint(text: str, code: str) -> str:
    if not _colors_enabled():
        return text
    return "\033[{}m{}\033[0m".format(code, text)


def info(message: str) -> None:
    print(message)


def warn(message: str) -> None:
    sys.stdout.flush()
    print(_paint("warning: ", "33;1") + message, file=sys.stderr)
    sys.stderr.flush()


def fail(message: str, code: int = 1) -> "NoReturn":  # noqa: F821
    sys.stdout.flush()
    print(_paint("error: ", "31;1") + message, file=sys.stderr)
    sys.stderr.flush()
    raise SystemExit(code)


def _progress_printer(target: Target):
    state = {"last": -1}

    def report(done: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        percent = int(done * 100 / total) if total else 0
        if percent == state["last"]:
            return
        state["last"] = percent
        sys.stderr.write(
            "\r  downloading {} {:>3}%  {}/{}   ".format(
                target.asset, percent, bundle.human_size(done), bundle.human_size(total)
            )
        )
        sys.stderr.flush()
        if done >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return report


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_setup(args: argparse.Namespace) -> int:
    if args.list:
        return _list_targets()

    target = parse_target(args.platform, args.arch)
    prefix = Path(args.prefix).expanduser() if args.prefix else bundle.default_prefix()

    cross = target != host_target()

    if cross and args.prefix is None:
        fail(
            "{} is not the platform you are on.\n"
            "  Installing another platform's binary needs an explicit location, e.g.\n"
            "  {} setup {} --prefix ./nvim-{}".format(
                target.key, PROG, target.family, target.key
            )
        )

    exe = prefix / "bin" / target.exe
    managed = bundle.is_managed(prefix, target.key)
    if exe.exists() and not managed and not args.force:
        fail(
            "{} already exists and was not installed by fast-nvim.\n"
            "  Re-run with --force to overwrite it, or pick another --prefix.".format(
                exe
            )
        )

    info("fast-nvim {} (Neovim {})".format(__version__, NVIM_VERSION))
    info("   target  {}".format(target.human))
    info("   prefix  {}".format(prefix))

    installed = bundle.installed_targets(prefix)

    if args.link and os.name == "nt" and not _can_symlink():
        warn("symlinks need Developer Mode or admin rights on Windows; copying instead")
        args.link = False

    try:
        source_dir, origin = bundle.ensure_bundle(
            target,
            offline=args.offline,
            progress=None
            if (args.quiet or args.dry_run)
            else _progress_printer(target),
        )
    except FastNvimError as exc:
        fail(str(exc))

    info("   source  {} ({})".format(origin, source_dir))
    if args.dry_run:
        info("")
        info("dry run: would install")
        for part, destination in bundle.BUNDLE_PARTS:
            if (source_dir / part).exists():
                info("  {}".format(prefix / destination))
    else:
        result = bundle.install(
            target, source_dir, prefix, link=args.link, dry_run=False
        )
        info("")
        info(
            "installed {} files ({})".format(
                result["files"], bundle.human_size(result["bytes"])
            )
        )
        for path in result["paths"]:
            info("  {}".format(path))
        if installed and target.key in installed and not args.force:
            info("updated the existing fast-nvim install in this prefix")

    if args.dry_run:
        return 0

    if not cross and not args.no_verify:
        try:
            version_line = bundle.neovim_version(exe)
        except FastNvimError as exc:
            fail("install finished but the binary does not run:\n{}".format(exc))
        info("")
        info("verified  {}".format(version_line))

    if not cross and not _on_path(prefix / "bin"):
        warn(
            "{} is not on your PATH. Add it for the current shell with:\n"
            '  export PATH="{}:$PATH"\n'
            "or permanently in ~/.bashrc / ~/.zshrc.".format(
                prefix / "bin", prefix / "bin"
            )
            if os.name != "nt"
            else "{} is not on your PATH; add it via System Properties > Environment Variables.".format(
                prefix / "bin"
            )
        )
    if cross:
        info("")
        info(
            "{} was installed for another platform; it cannot be verified here.".format(
                target.key
            )
        )
    return 0


def _on_path(directory: Path) -> bool:
    wanted = os.path.normcase(os.path.abspath(str(directory)))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and os.path.normcase(os.path.abspath(entry)) == wanted:
            return True
    return False


def _can_symlink() -> bool:
    try:
        with __import__("tempfile").TemporaryDirectory() as tmp:
            os.symlink(tmp, os.path.join(tmp, "probe"))
        return True
    except OSError:
        return False


def _list_targets() -> int:
    host = host_target()
    width = max(len(key) for key in TARGET_KEYS)
    info("{:<{w}}  {:<9}  {}".format("platform", "status", "notes", w=width))
    for key in TARGET_KEYS:
        target = TARGETS[key]
        notes = []
        if host and target.key == host.key:
            notes.append("this machine")
        if bundle.has_bundled(target):
            status = "bundled"
        elif (bundle.cached_tree(target) / "bin" / target.exe).is_file():
            status = "cached"
        else:
            status = "download"
            notes.append("needs network")
        info("{:<{w}}  {:<9}  {}".format(key, status, ", ".join(notes), w=width))
    info("")
    info("Neovim {}, cache in {}".format(NVIM_VERSION, bundle.cache_root()))
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    target = parse_target(args.platform, args.arch)
    payload: Dict[str, object] = {"target": target.key}
    if args.bundle:
        payload["bundle"] = str(bundle.bundle_path(target.key))
    else:
        prefix = (
            Path(args.prefix).expanduser() if args.prefix else bundle.default_prefix()
        )
        exe = prefix / "bin" / target.exe
        if args.installed_only and not bundle.is_managed(prefix, target.key):
            fail(
                "no fast-nvim install for {} in {}\n  run: {} setup {}".format(
                    target.key, prefix, PROG, target.key
                )
            )
        payload["prefix"] = str(prefix)
        payload["exe"] = str(exe)
        payload["installed"] = bundle.is_managed(prefix, target.key)
        if args.json:
            payload["bundle"] = str(bundle.bundle_path(target.key))
            payload["bundled"] = bundle.has_bundled(target)
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
    elif args.bundle:
        info(str(bundle.bundle_path(target.key)))
    else:
        info(str(payload["exe"]))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    target = parse_target(args.platform, args.arch)
    info_data = bundle.read_build_info()
    payload: Dict[str, object] = {
        "fast_nvim": __version__,
        "nvim_release": NVIM_VERSION,
        "target": target.key,
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "bundled": bundle.has_bundled(target),
        "cache": str(bundle.cache_root()),
        "build_info": info_data,
        "vendored_targets": bundle.vendored_keys(),
    }
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    info("fast-nvim     {}".format(__version__))
    info("Neovim        {} (pinned release)".format(NVIM_VERSION))
    info("target        {}".format(target.human))
    vendored = bundle.vendored_keys()
    info("in this wheel {}".format(", ".join(vendored) if vendored else "no binaries!"))
    if bundle.has_bundled(target):
        try:
            info(
                "bundled nvim  {}".format(
                    bundle.neovim_version(bundle.bundle_exe(target.key))
                )
            )
        except FastNvimError as exc:
            info("bundled nvim  unusable: {}".format(exc))
    return 0


def cmd_run(argv: Sequence[str]) -> int:
    """`fast-nvim run [--] [nvim args...]` -- exec the bundled binary."""
    args = list(argv)
    if args and args[0] == "--":
        args = args[1:]
    target = host_target()
    if target is None:
        fail("cannot detect this platform; use `{} setup` instead".format(PROG))
    if not bundle.has_bundled(target):
        fail(
            "this fast-nvim install has no Neovim binary for {}.\n"
            "  installed targets: {}\n"
            "  run: {} setup {}".format(
                target.key,
                ", ".join(bundle.vendored_keys()) or "none",
                PROG,
                target.key,
            )
        )
    exe = bundle.bundle_exe(target.key)
    bundle.ensure_executable(exe)
    if os.name == "nt":
        return subprocess.call([str(exe)] + args)
    os.execv(str(exe), [str(exe)] + args)  # replaces this process
    return 0  # pragma: no cover


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Ship Neovim in a pip wheel: install the bundled nvim, or fetch "
        "another platform's build.",
    )
    parser.add_argument(
        "--version", action="version", version="{} {}".format(PROG, __version__)
    )
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser(
        "setup",
        help="install the bundled nvim into a prefix (default: ~/.local)",
        description="Install Neovim into a prefix so `nvim` is usable on PATH. "
        "The platform argument defaults to this machine; other platforms are "
        "downloaded from GitHub (and cached).",
    )
    setup.add_argument(
        "platform",
        nargs="?",
        default=None,
        metavar="PLATFORM",
        help="linux, mac, windows, or an exact build like linux-arm64 "
        "(default: this machine)",
    )
    setup.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    setup.add_argument("--prefix", help="install prefix (default: ~/.local)")
    setup.add_argument("--force", action="store_true", help="overwrite existing files")
    setup.add_argument(
        "--link",
        action="store_true",
        help="symlink into the package instead of copying (breaks if the package is removed)",
    )
    setup.add_argument("--dry-run", action="store_true", help="show what would happen")
    setup.add_argument(
        "--no-verify", action="store_true", help="skip the post-install smoke test"
    )
    setup.add_argument(
        "--offline",
        action="store_true",
        help="never download; use bundled/cached copies only",
    )
    setup.add_argument("--quiet", action="store_true", help="no progress output")
    setup.add_argument(
        "--list", action="store_true", help="list known platforms and their status"
    )
    setup.set_defaults(func=cmd_setup)

    path = subparsers.add_parser(
        "path", help="print the nvim path (or the bundled bundle directory)"
    )
    path.add_argument("platform", nargs="?", default=None, metavar="PLATFORM")
    path.add_argument("--arch")
    path.add_argument("--prefix", help="prefix to inspect (default: ~/.local)")
    path.add_argument(
        "--bundle", action="store_true", help="print the in-package bundle dir"
    )
    path.add_argument(
        "--installed-only",
        action="store_true",
        help="fail unless fast-nvim installed this target into the prefix",
    )
    path.add_argument("--json", action="store_true")
    path.set_defaults(func=cmd_path)

    version = subparsers.add_parser("version", help="show versions and what is bundled")
    version.add_argument("platform", nargs="?", default=None, metavar="PLATFORM")
    version.add_argument("--arch")
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)

    run = subparsers.add_parser(
        "run",
        help="run the bundled nvim (all arguments go to nvim)",
        description="Run the Neovim binary shipped in this wheel without installing "
        "it. Everything after `run` is passed to nvim verbatim.",
    )
    run.set_defaults(func=None)

    return parser


HELP = """{prog} -- ship Neovim in a pip wheel

usage:
  {prog} setup [linux|mac|windows|auto] [--prefix DIR] [--force] [--link]
  {prog} setup --list
  {prog} path [--prefix DIR] [--bundle] [--json]
  {prog} version [--json]
  {prog} run [--] [nvim args...]

examples:
  pip install fast-nvim
  {prog} setup                 # ~/.local/bin/nvim + runtime
  {prog} setup windows --prefix ./dist-win
  {prog} run -- --version
""".format(prog=PROG)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if args[0] == "run":
        return cmd_run(args[1:])

    parser = build_parser()
    parsed = parser.parse_args(args)
    if not getattr(parsed, "func", None):
        print(HELP)
        return 0
    try:
        return int(parsed.func(parsed) or 0)
    except FastNvimError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
    return 0
