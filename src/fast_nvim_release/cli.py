"""Command line interface: ``fast-nvim-release <command>``."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, NoReturn, Optional, Sequence, Tuple

from . import __version__, bundle
from .bundle import FastNvimError
from .platforms import (
    DEFAULT_RELEASE,
    RELEASE_KEYS,
    RELEASES,
    Target,
    emulation,
    emulation_note,
    get_release,
    glibc_problem,
    host_platform,
    is_cross,
    looks_like_release,
    parse_release,
    parse_target,
)

PROG = "fast-nvim-release"

EXAMPLES = """examples:
  pip install fast-nvim-release
  fast-nvim-release setup --prefix ~/.local            # newest bundled release
  fast-nvim-release setup 0.10 --prefix ~/nvim-0-10    # an older Neovim line
  fast-nvim-release setup 0.12 windows --prefix ./dist-win
  fast-nvim-release run 0.10 -- --version
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


def _looks_numeric(value: str) -> bool:
    """True for input the user almost certainly meant as a release."""
    text = value.strip().lower()
    if text.startswith("v"):
        text = text[1:]
    return bool(text) and text[0].isdigit()


def _split_selectors(selectors: Sequence[str]) -> Tuple[str, Optional[str]]:
    """Classify positional arguments into ``(release, platform)``.

    ``setup 0.10``, ``setup windows``, ``setup 0.10 windows`` and
    ``setup windows 0.10`` all work; order does not matter.
    """
    release_key: Optional[str] = None
    platform_spec: Optional[str] = None
    for value in selectors:
        if looks_like_release(value) or _looks_numeric(value):
            # a numeric argument is a release even when it is not one we know,
            # so the error says "unknown Neovim release", not "unknown platform"
            if release_key is not None:
                fail(f"two releases given ({release_key}, {parse_release(value)})")
            release_key = parse_release(value)
            continue
        if platform_spec is not None:
            fail(f"two platforms given ({platform_spec}, {value})")
        platform_spec = value
    return release_key or DEFAULT_RELEASE, platform_spec


def _installed_summary(prefix: Path) -> str:
    builds = bundle.installed_targets(prefix)
    if not builds:
        return "nothing"
    return ", ".join(f"{t.release.display} ({t.key})" for t in builds)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_setup(args: argparse.Namespace) -> int:
    if args.list:
        return _list_releases()

    release_key, platform_spec = _split_selectors(args.selectors)
    target = parse_target(platform_spec, args.arch, release_key)
    if not args.prefix:
        fail(
            "setup needs an explicit --prefix; there is no default install location.\n"
            f"  e.g. {PROG} setup --prefix ~/.local"
        )
    prefix = bundle.normalize(args.prefix)

    cross = is_cross(target)
    problem = glibc_problem(target)
    if problem:
        fail(problem)

    managed = bundle.is_managed(prefix, target.release_key, target.key)
    others = [
        build
        for build in bundle.installed_builds(prefix)
        if build != (target.release_key, target.key)
    ]
    exe = prefix / "bin" / target.exe
    if exe.exists() and not managed and not others and not args.force:
        fail(
            f"{exe} already exists and was not installed by {PROG}.\n"
            "  Re-run with --force to overwrite it, or pick another --prefix."
        )
    if not managed and not others and not args.force:
        for owned in (prefix / "lib" / "nvim", prefix / "share" / "nvim" / "runtime"):
            if owned.exists():
                fail(
                    f"{owned} already exists and was not installed by {PROG}.\n"
                    "  Re-run with --force to replace it, or pick another --prefix."
                )

    info(f"{PROG} {__version__} (Neovim {target.nvim_version})")
    info(f"   release {target.release_key} ({target.nvim_version})")
    info(f"   target  {target.platform.human}")
    info(f"   prefix  {prefix}")
    if cross:
        _warn_if_replacing_this_machines_build(prefix, target)
    note = emulation_note()
    if note and not cross:
        warn(note)

    if args.dry_run:
        origin, where = bundle.origin(target)
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
        hint = target.release.platform_note(target.key)
        info(f"   fetch   {target.asset} (Neovim {target.nvim_version})")
        if hint:
            info(f"           {hint}")

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
    if managed and not args.force:
        info("updated the existing fast-nvim-release install in this prefix")
    elif others:
        previous = ", ".join(f"{r} ({p})" for r, p in others)
        info(f"replaced {previous} in this prefix (one Neovim per prefix)")

    if not cross and not args.no_verify:
        try:
            version_line = bundle.verify_install(exe, prefix)
        except FastNvimError as exc:
            fail(f"install finished but the binary does not work:\n{exc}")
        info("")
        info(f"verified  {version_line}")

    bin_dir = prefix / "bin"
    if not cross and not _on_path(bin_dir):
        warn(
            f"{bin_dir} is not on your PATH. Add it for the current shell with:\n"
            f'  export PATH="{bin_dir}:$PATH"\n'
            "or permanently in ~/.bashrc / ~/.zshrc."
            if os.name != "nt"
            else f"{bin_dir} is not on your PATH; add it via System Properties > "
            "Environment Variables."
        )
    if cross:
        info("")
        info(f"{target.key} was installed for another platform; it cannot be verified here.")
    return 0


def _warn_if_replacing_this_machines_build(prefix: Path, target: Target) -> None:
    """Say so when a cross-platform install takes over the host's prefix.

    A prefix holds one Neovim, so a cross-platform build aimed at a prefix that
    already holds this machine's build silently leaves the user without a Neovim
    for the machine they are sitting at.  The install still happens -- it was
    asked for -- but not quietly.
    """
    here = host_platform()
    if here is None or target.key == here.key:
        return
    losses = [build for build in bundle.installed_targets(prefix) if build.key == here.key]
    if not losses:
        return
    warn(
        "{} already holds a Neovim for this machine ({}); installing {} replaces it,\n"
        "  so `nvim` will no longer run here. Use a separate --prefix to keep both.".format(
            prefix, ", ".join(build.label for build in losses), target.label
        )
    )


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


#: The words a table column uses for each of bundle.origin()'s answers.
_STATUS_WORDS = {"pip wheel": "bundled", "cache": "cached"}


def _release_status(release_key: str, platform_key: str) -> str:
    """How ``release_key`` would be obtained for ``platform_key``."""
    target = RELEASES[release_key].target(platform_key)
    if target is None:
        return "unavailable"  # Neovim never published it for that platform
    kind, _where = bundle.origin(target)
    return _STATUS_WORDS.get(kind, kind)


def _list_releases() -> int:
    here = host_platform()
    here_key = here.key if here else "(unknown)"
    rows: List[Tuple[str, str, str, str]] = []
    for key in RELEASE_KEYS:
        release = RELEASES[key]
        status = _release_status(key, here_key)
        if key == DEFAULT_RELEASE:
            status += " (default)"
        bundled = [t.key for t in release.targets if bundle.has_bundled(t)]
        rows.append((key, release.version, status, ", ".join(bundled) or "none"))
    width = max([len("release")] + [len(row[0]) for row in rows])
    info("{:<{w}}  {:<8}  {:<24}  {}".format("release", "neovim", here_key, "bundled", w=width))
    for release_key, version, status, bundled in rows:
        info("{:<{w}}  {:<8}  {:<24}  {}".format(release_key, version, status, bundled, w=width))
    info("")
    info("older lines are fetched from GitHub (checksum-pinned) on first use")
    info(f"cache in {bundle.cache_root()}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    release_key, platform_spec = _split_selectors(args.selectors)
    target = parse_target(platform_spec, args.arch, release_key)
    bundled = bundle.has_bundled(target)
    if args.bundle:
        if not bundled:
            own = ", ".join(bundle.vendored_platforms(target.release_key)) or "none"
            fail(
                f"Neovim {target.nvim_version} is not bundled for {target.key} "
                f"in this wheel (bundled: {own})\n"
                f"  run: {PROG} setup {target.release_key} {target.key}"
            )
        payload: Dict[str, object] = {
            "release": target.release_key,
            "nvim_version": target.nvim_version,
            "target": target.key,
            "bundle": str(bundle.bundle_path(target.release_key, target.key)),
            "bundled": True,
        }
        info(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["bundle"])
        return 0

    prefix = bundle.normalize(args.prefix) if args.prefix else bundle.default_prefix()
    exe = prefix / "bin" / target.exe
    complete = bundle.is_complete(prefix, target.release_key, target.key)
    if args.installed_only and not complete:
        fail(
            f"no fast-nvim-release install of Neovim {target.nvim_version} for "
            f"{target.key} in {prefix}\n"
            f"  run: {PROG} setup {target.release_key}"
        )
    payload = {
        "release": target.release_key,
        "nvim_version": target.nvim_version,
        "target": target.key,
        "prefix": str(prefix),
        "exe": str(exe),
        "installed": complete,
        "managed": bundle.is_managed(prefix, target.release_key, target.key),
        "bundled": bundled,
        "bundle": str(bundle.bundle_path(target.release_key, target.key)),
        "installed_releases": bundle.installed_releases(prefix),
    }
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
    else:
        info(str(exe))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    release_key, platform_spec = _split_selectors(args.selectors)
    target = parse_target(platform_spec, args.arch, release_key)
    bundled = bundle.vendored_targets()
    prefix = bundle.normalize(args.prefix) if args.prefix else bundle.default_prefix()
    payload: Dict[str, object] = {
        "fast_nvim_release": __version__,
        "default_release": DEFAULT_RELEASE,
        "target": target.key,
        "release": target.release_key,
        "nvim_version": target.nvim_version,
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "python_arch": platform.machine(),
        "emulated": bool(emulation()),
        "bundled": bundle.has_bundled(target),
        "cache": str(bundle.cache_root()),
        "prefix": str(prefix),
        "installed": [(t.release_key, t.key) for t in bundle.installed_targets(prefix)],
        "releases": {
            key: {
                "nvim_version": RELEASES[key].version,
                "platforms": [t.key for t in RELEASES[key].targets],
                "bundled": bundle.vendored_platforms(key),
            }
            for key in RELEASE_KEYS
        },
        "vendored_targets": [t.label for t in bundled],
    }
    if args.json:
        info(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    lines = [f"{RELEASES[key].key} - {RELEASES[key].version}" for key in RELEASE_KEYS]
    rows = [
        (PROG, __version__),
        ("releases", ", ".join(lines)),
        ("default", f"{DEFAULT_RELEASE} ({RELEASES[DEFAULT_RELEASE].version})"),
        ("target", f"{target.release_key} on {target.platform.human}"),
        (
            "in this wheel",
            ", ".join(t.label for t in bundled) if bundled else "no binaries!",
        ),
        ("installed", f"{_installed_summary(prefix)} ({prefix})"),
    ]
    found = emulation()
    if found:
        rows.append(("python", f"{found[0]} (emulated on {found[1]} hardware)"))
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        info(f"{label:<{width}}  {value}")
    if bundle.has_bundled(target):
        try:
            bundled_exe = bundle.bundle_exe(target.release_key, target.key)
            bundle.ensure_executable(bundled_exe)  # pip may not restore the exec bit
            info(f"{'bundled nvim':<{width}}  {bundle.neovim_version(bundled_exe)}")
        except FastNvimError as exc:
            info(f"{'bundled nvim':<{width}}  unusable: {exc}")
    return 0


def cmd_run(argv: Sequence[str]) -> int:
    """``fast-nvim-release run [RELEASE] [--] [nvim args...]``."""
    args = list(argv)
    release_key = DEFAULT_RELEASE
    if args and args[0] != "--" and looks_like_release(args[0]):
        release_key = parse_release(args[0])
        args = args[1:]
    if args and args[0] == "--":
        args = args[1:]

    here = host_platform()
    if here is None:
        fail(f"cannot detect this platform; use `{PROG} setup` instead")
    target = get_release(release_key).target(here.key)
    if target is None:
        fail(
            "Neovim {} has no {} build.\n  try one of: {}".format(
                get_release(release_key).version,
                here.key,
                ", ".join(p.key for p in get_release(release_key).platforms),
            )
        )
    if not bundle.has_bundled(target):
        own = ", ".join(bundle.vendored_platforms(target.release_key)) or "none"
        fail(
            f"this wheel does not bundle Neovim {target.nvim_version} for {target.key}.\n"
            f"  bundled for {target.release_key}: {own}\n"
            f"  run: {PROG} setup {target.release_key} --prefix <dir>"
        )
    exe = bundle.bundle_exe(target.release_key, target.key)
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
        description="Ship Neovim releases in a pip wheel: install the bundled build "
        "of the release you pick, or fetch another platform's.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    selector_help = (
        "release (0.10, 0.11, 0.12, latest) and/or platform (linux, mac, windows, "
        "linux-arm64). Both are optional and may be given in either order; the "
        f"release defaults to {DEFAULT_RELEASE} and the platform to this machine."
    )

    setup = subparsers.add_parser(
        "setup",
        help="install a release into a prefix (default: newest release)",
        description="Install Neovim into a prefix so `nvim` is usable on PATH. "
        "The prefix is always explicit, so nothing is overwritten by accident. "
        "Older release lines come from GitHub (checksum-pinned) and are cached.",
    )
    setup.add_argument("selectors", nargs="*", metavar="RELEASE|PLATFORM", help=selector_help)
    setup.add_argument("--arch", help="x86_64/amd64 or arm64/aarch64")
    # not `required=True`: `setup --list` installs nothing and needs no prefix
    setup.add_argument(
        "--prefix",
        help="install prefix (required unless --list; there is no default)",
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
    setup.add_argument(
        "--list", action="store_true", help="list supported releases and where they come from"
    )
    setup.set_defaults(func=cmd_setup)

    path = subparsers.add_parser(
        "path", help="print the nvim path (or the in-package bundle directory)"
    )
    path.add_argument("selectors", nargs="*", metavar="RELEASE|PLATFORM", help=selector_help)
    path.add_argument("--arch")
    path.add_argument("--prefix", help="prefix to inspect (default: ~/.local)")
    path.add_argument("--bundle", action="store_true", help="print the in-package bundle dir")
    path.add_argument(
        "--installed-only",
        action="store_true",
        help="fail unless fast-nvim-release installed this release into the prefix",
    )
    path.add_argument("--json", action="store_true")
    path.set_defaults(func=cmd_path)

    version = subparsers.add_parser(
        "version", help="show versions, supported releases, and what is bundled"
    )
    version.add_argument("selectors", nargs="*", metavar="RELEASE|PLATFORM", help=selector_help)
    version.add_argument("--arch")
    version.add_argument("--prefix", help="prefix to inspect (default: ~/.local)")
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=cmd_version)

    # `main` handles `run` itself, so this parser is never used to parse
    # arguments -- it exists so the command (and its help text) shows up in
    # `--help`.  Anything after `run` belongs to Neovim, `--help` included.
    run = subparsers.add_parser(
        "run",
        help="run a bundled nvim without installing it",
        description="Run the Neovim binary shipped in this wheel without installing "
        "it. Everything after `run` (and the optional release) is passed to nvim "
        "verbatim.",
    )
    run.add_argument(
        "nvim_args",
        nargs=argparse.REMAINDER,
        help="optional RELEASE followed by arguments passed to nvim verbatim",
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
