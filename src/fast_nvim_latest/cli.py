"""Command line interface: ``fast-nvim-latest <command>``."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, NoReturn, Optional, Sequence, Tuple

from . import __version__, bundle
from .bundle import FastNvimError
from .platforms import (
    NVIM_VERSION,
    TARGET_KEYS,
    TARGETS,
    Target,
    emulation,
    emulation_note,
    host_target,
    parse_target,
)

PROG = "fast-nvim-latest"

EXAMPLES = """examples:
  pip install fast-nvim-latest
  fast-nvim-latest setup                 # ~/.local/bin/nvim + runtime
  fast-nvim-latest setup windows --prefix ./dist-win
  fast-nvim-latest run -- --version
"""


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
    return f"\033[{code}m{text}\033[0m"


def info(message: str) -> None:
    print(message)


def warn(message: str) -> None:
    sys.stdout.flush()
    print(_paint("warning: ", "33;1") + message, file=sys.stderr)
    sys.stderr.flush()


def fail(message: str, code: int = 1) -> NoReturn:
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
        done_text = bundle.human_size(done)
        total_text = bundle.human_size(total)
        sys.stderr.write(
            f"\r  downloading {target.asset} {percent:>3}%  {done_text}/{total_text}   "
        )
        sys.stderr.flush()
        if done >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return report


def _origin(target: Target) -> Tuple[str, Optional[Path]]:
    """Where a build would come from, without fetching anything."""
    if bundle.has_bundled(target):
        return "pip wheel", bundle.bundle_path(target.key)
    tree = bundle.cached_tree(target)
    if (tree / "bin" / target.exe).is_file():
        return "cache", tree
    archive = bundle.cached_archive(target)
    if archive.is_file():
        return "cached archive", archive
    return "download", None


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_setup(args: argparse.Namespace) -> int:
    if args.list:
        return _list_targets()

    target = parse_target(args.platform, args.arch)
    prefix = Path(args.prefix).expanduser() if args.prefix else bundle.default_prefix()

    cross = target != host_target()
    explicit_location = bool(args.prefix) or bool(
        os.environ.get("FAST_NVIM_LATEST_PREFIX", "").strip()
    )

    if cross and not explicit_location:
        fail(
            f"{target.key} is not the platform you are on.\n"
            "  Installing another platform's binary needs an explicit location, e.g.\n"
            f"  {PROG} setup {target.family} --prefix ./nvim-{target.key}"
        )

    exe = prefix / "bin" / target.exe
    managed = bundle.is_managed(prefix, target.key)
    if exe.exists() and not managed and not args.force:
        fail(
            f"{exe} already exists and was not installed by fast-nvim-latest.\n"
            "  Re-run with --force to overwrite it, or pick another --prefix."
        )
    if not managed and not args.force:
        others = [key for key in bundle.installed_targets(prefix) if key != target.key]
        for owned in (prefix / "lib" / "nvim", prefix / "share" / "nvim" / "runtime"):
            if owned.exists():
                if others:
                    fail(
                        f"{owned} belongs to the fast-nvim-latest install for "
                        f"{', '.join(others)}; a prefix holds one Neovim build.\n"
                        "  Use another --prefix, or --force to replace it."
                    )
                fail(
                    f"{owned} already exists and was not installed by fast-nvim-latest.\n"
                    "  Re-run with --force to replace it, or pick another --prefix."
                )

    info(f"fast-nvim-latest {__version__} (Neovim {NVIM_VERSION})")
    info(f"   target  {target.human}")
    info(f"   prefix  {prefix}")
    note = emulation_note()
    if note and not cross:
        warn(note)

    installed = bundle.installed_targets(prefix)

    if args.dry_run:
        origin, where = _origin(target)
        info("   source  {}{}".format(origin, f" ({where})" if where else " (not fetched yet)"))
        info("")
        info("dry run: would install")
        for _, destination in bundle.BUNDLE_PARTS:
            info(f"  {prefix / destination}")
        return 0

    if args.link and os.name == "nt" and not _can_symlink():
        warn("symlinks need Developer Mode or admin rights on Windows; copying instead")
        args.link = False

    if bundle.needs_download(target) and not args.offline:
        info(f"   fetch   {target.asset} (Neovim {NVIM_VERSION})")

    try:
        source_dir, origin = bundle.ensure_bundle(
            target,
            offline=args.offline,
            progress=None if args.quiet else _progress_printer(target),
        )
    except FastNvimError as exc:
        fail(str(exc))

    info(f"   source  {origin} ({source_dir})")

    try:
        result = bundle.install(
            target,
            source_dir,
            prefix,
            link=args.link,
            replace=bool(args.force) and not managed,
        )
    except FastNvimError as exc:
        fail(str(exc))

    info("")
    if result["links"]:
        info("linked {} entries (no copy; --link)".format(result["links"]))
    else:
        info("installed {} files ({})".format(result["files"], bundle.human_size(result["bytes"])))
    for path in result["paths"]:
        info(f"  {path}")
    if installed and target.key in installed and not args.force:
        info("updated the existing fast-nvim-latest install in this prefix")

    if not cross and not args.no_verify:
        try:
            version_line = bundle.verify_install(exe, prefix)
        except FastNvimError as exc:
            fail(f"install finished but the binary does not work:\n{exc}")
        info("")
        info(f"verified  {version_line}")

    if not cross and not _on_path(prefix / "bin"):
        warn(
            "{} is not on your PATH. Add it for the current shell with:\n"
            '  export PATH="{}:$PATH"\n'
            "or permanently in ~/.bashrc / ~/.zshrc.".format(prefix / "bin", prefix / "bin")
            if os.name != "nt"
            else "{} is not on your PATH; add it via System Properties > "
            "Environment Variables.".format(prefix / "bin")
        )
    if cross:
        info("")
        info(f"{target.key} was installed for another platform; it cannot be verified here.")
    return 0


def _on_path(directory: Path) -> bool:
    wanted = os.path.normcase(os.path.abspath(str(directory)))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and os.path.normcase(os.path.abspath(entry)) == wanted:
            return True
    return False


def _can_symlink() -> bool:
    try:
        with tempfile.TemporaryDirectory() as tmp:
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
        elif bundle.cached_archive(target).is_file():
            status = "cached"
            notes.append("archive only, needs unpacking")
        else:
            status = "download"
            notes.append("needs network")
        info("{:<{w}}  {:<9}  {}".format(key, status, ", ".join(notes), w=width))
    info("")
    info(f"Neovim {NVIM_VERSION}, cache in {bundle.cache_root()}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    target = parse_target(args.platform, args.arch)
    bundled = bundle.has_bundled(target)
    if args.bundle:
        if not bundled:
            fail(
                "{} is not bundled in this wheel (bundled: {})\n  run: {} setup {}".format(
                    target.key,
                    ", ".join(bundle.vendored_keys()) or "none",
                    PROG,
                    target.key,
                )
            )
        payload: Dict[str, object] = {
            "target": target.key,
            "bundle": str(bundle.bundle_path(target.key)),
            "bundled": True,
        }
        info(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["bundle"])
        return 0

    prefix = Path(args.prefix).expanduser() if args.prefix else bundle.default_prefix()
    exe = prefix / "bin" / target.exe
    complete = bundle.is_complete(prefix, target.key)
    if args.installed_only and not complete:
        fail(
            f"no fast-nvim-latest install for {target.key} in {prefix}\n"
            f"  run: {PROG} setup {target.key}"
        )
    payload = {
        "target": target.key,
        "prefix": str(prefix),
        "exe": str(exe),
        "installed": complete,
        "managed": bundle.is_managed(prefix, target.key),
        "bundled": bundled,
        "bundle": str(bundle.bundle_path(target.key)),
    }
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
    else:
        info(str(exe))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    target = parse_target(args.platform, args.arch)
    payload: Dict[str, object] = {
        "fast_nvim_latest": __version__,
        "nvim_release": NVIM_VERSION,
        "target": target.key,
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "python_arch": platform.machine(),
        "emulated": bool(emulation()),
        "bundled": bundle.has_bundled(target),
        "cache": str(bundle.cache_root()),
        "build_info": bundle.read_build_info(),
        "vendored_targets": bundle.vendored_keys(),
    }
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    vendored = bundle.vendored_keys()
    rows = [
        (PROG, __version__),
        ("Neovim", f"{NVIM_VERSION} (pinned release)"),
        ("target", target.human),
        ("in this wheel", ", ".join(vendored) if vendored else "no binaries!"),
    ]
    found = emulation()
    if found:
        rows.append(("python", f"{found[0]} (emulated on {found[1]} hardware)"))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        info(f"{label:<{width}}  {value}")
    if bundle.has_bundled(target):
        try:
            info(
                f"{'bundled nvim':<{width}}  {bundle.neovim_version(bundle.bundle_exe(target.key))}"
            )
        except FastNvimError as exc:
            info(f"{'bundled nvim':<{width}}  unusable: {exc}")
    return 0


def cmd_run(argv: Sequence[str]) -> int:
    """`fast-nvim-latest run [--] [nvim args...]` -- exec the bundled binary."""
    args = list(argv)
    if args and args[0] == "--":
        args = args[1:]
    target = host_target()
    if target is None:
        fail(f"cannot detect this platform; use `{PROG} setup` instead")
    if not bundle.has_bundled(target):
        fail(
            "this fast-nvim-latest wheel has no Neovim binary for {}.\n"
            "  bundled targets: {}\n"
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
        return subprocess.call([str(exe), *args])
    os.execv(str(exe), [str(exe), *args])  # replaces this process
    return 0  # pragma: no cover


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Ship Neovim in a pip wheel: install the bundled nvim, or fetch "
        "another platform's build.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

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
        help="linux, mac, windows, or an exact build like linux-arm64 (default: this machine)",
    )
    setup.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    setup.add_argument(
        "--prefix",
        help="install prefix (default: $FAST_NVIM_LATEST_PREFIX or ~/.local)",
    )
    setup.add_argument("--force", action="store_true", help="overwrite existing files")
    setup.add_argument(
        "--link",
        action="store_true",
        help="symlink into the package instead of copying (breaks if the package is removed)",
    )
    setup.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be written, without fetching or writing anything",
    )
    setup.add_argument("--no-verify", action="store_true", help="skip the post-install smoke test")
    setup.add_argument(
        "--offline",
        action="store_true",
        help="never download; use bundled/cached copies only",
    )
    setup.add_argument("--quiet", action="store_true", help="no progress bar")
    setup.add_argument("--list", action="store_true", help="list known platforms and their status")
    setup.set_defaults(func=cmd_setup)

    path = subparsers.add_parser(
        "path", help="print the nvim path (or the bundled bundle directory)"
    )
    path.add_argument("platform", nargs="?", default=None, metavar="PLATFORM")
    path.add_argument("--arch")
    path.add_argument("--prefix", help="prefix to inspect (default: ~/.local)")
    path.add_argument("--bundle", action="store_true", help="print the in-package bundle dir")
    path.add_argument(
        "--installed-only",
        action="store_true",
        help="fail unless fast-nvim-latest installed this target into the prefix",
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
    run.add_argument(
        "nvim_args",
        nargs=argparse.REMAINDER,
        help="arguments passed to nvim verbatim",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `run` hands everything after it to Neovim, so it never goes through
    # argparse (which would eat nvim's own --flags).
    if args and args[0] == "run":
        return cmd_run(args[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(parser.format_help().rstrip())
        return 0

    parsed = parser.parse_args(args)
    func = getattr(parsed, "func", None)
    if func is None:  # pragma: no cover - subparsers always set one
        print(parser.format_help().rstrip())
        return 0
    try:
        return int(func(parsed) or 0)
    except FastNvimError as exc:
        fail(str(exc))
    except ValueError as exc:
        fail(str(exc))
    except OSError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
    return 0
