"""Find the Neovim build inside the wheel and install it into a prefix.

Each wheel carries one complete Neovim tree, for one line and one platform::

    fast_nvim_release/_nvim/bin/nvim
    fast_nvim_release/_nvim/lib/nvim/...
    fast_nvim_release/_nvim/share/nvim/runtime/...
    fast_nvim_release/_nvim/build.json        which build this is

Neovim finds its runtime relative to its own executable
(``../share/nvim/runtime``), so ``setup`` copies the tree into the prefix with
that layout intact.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Set, Union

from .platforms import PLATFORMS, Platform, host_platform

BUNDLE_ROOT = Path(__file__).resolve().parent / "_nvim"

#: What ``setup`` puts into a prefix, besides the marker.
PARTS = ("bin", "lib/nvim", "share/nvim/runtime")

#: ``<prefix>/share/fast-nvim-release/``: records what this tool installed.
#: Releases before 0.4 wrote ``<release>-<platform>.json`` files here; every
#: ``*.json`` in the directory is read, so those prefixes stay recognised.
MARKER_DIR = Path("share") / "fast-nvim-release"
MARKER = MARKER_DIR / "install.json"


class FastNvimError(RuntimeError):
    """Something the user can fix; reported without a traceback."""


# --------------------------------------------------------------------------
# the build inside this wheel
# --------------------------------------------------------------------------
def build_info() -> Dict[str, str]:
    """``build.json``: the line, Neovim version, platform and tag of this wheel."""
    try:
        data = json.loads((BUNDLE_ROOT / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def bundled_platform() -> Platform:
    """The platform of the bundled build (this machine's, if unrecorded)."""
    key = build_info().get("platform", "")
    if key in PLATFORMS:
        return PLATFORMS[key]
    here = host_platform()
    if here is None:
        raise FastNvimError("there is no Neovim build for this platform")
    return here


def bundled_exe() -> Path:
    """The bundled executable, or a :class:`FastNvimError` saying why there is none."""
    exe = BUNDLE_ROOT / "bin" / bundled_platform().exe
    if not exe.is_file():
        raise FastNvimError(
            "this installation carries no Neovim build "
            "(built from source without running scripts/fetch_nvim.py first?)"
        )
    return exe


# --------------------------------------------------------------------------
# prefixes
# --------------------------------------------------------------------------
def normalize(path: Union[str, os.PathLike]) -> Path:
    """Absolute, ``~``-expanded form of a user supplied path."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def default_prefix() -> Path:
    """Where ``path`` looks when no ``--prefix`` is given (``setup`` has no default)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return normalize(Path(base) / "fast-nvim-release")
    return normalize(Path.home() / ".local")


def _markers(prefix: Path) -> List[Dict[str, object]]:
    found = []
    for path in sorted((prefix / MARKER_DIR).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            found.append(data)
    return found


def installed(prefix: Path) -> Dict[str, object]:
    """What this tool installed into ``prefix`` (``{}`` if nothing)."""
    markers = _markers(prefix)
    return markers[0] if markers else {}


def _owned_bin_names(prefix: Path) -> Set[str]:
    """Entries of ``<prefix>/bin`` that a marker says this tool put there.

    Markers are files users can edit, so only plain file names count.
    """
    names: Set[str] = set()
    for data in _markers(prefix):
        listed = data.get("bin")
        if isinstance(listed, list):
            names.update(
                name
                for name in listed
                if isinstance(name, str)
                and name not in ("", ".", "..")
                and "/" not in name
                and "\\" not in name
            )
    return names


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy(source: Path, dest: Path) -> None:
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, dest, symlinks=True)
    else:
        shutil.copy2(source, dest, follow_symlinks=False)


def _write_marker(prefix: Path, bin_names: Iterable[str]) -> None:
    marker = prefix / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    info = build_info()
    data = {
        "line": info.get("line"),
        "nvim_version": info.get("neovim"),
        "platform": bundled_platform().key,
        "bin": sorted(bin_names),
    }
    marker.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install(prefix: Path, *, force: bool = False) -> Path:
    """Copy the bundled build into ``prefix`` and return the installed executable.

    A prefix holds one Neovim: installing into a prefix this tool already
    manages replaces the previous build, including the ``bin`` entries it
    owned.  Anything else is never overwritten without ``force``.
    """
    exe = bundled_exe()
    source = exe.parent.parent
    prefix = normalize(prefix)
    names = sorted(entry.name for entry in (source / "bin").iterdir())
    owned = _owned_bin_names(prefix)
    managed = bool(_markers(prefix))

    if not force:
        # Windows file names ignore case, and releases differ in how they spell
        # the same DLL (dbghelp.dll vs DbgHelp.dll)
        owned_keys = {os.path.normcase(name) for name in owned}
        foreign = [
            prefix / "bin" / name
            for name in names
            if os.path.normcase(name) not in owned_keys
            and ((prefix / "bin" / name).exists() or (prefix / "bin" / name).is_symlink())
        ]
        if not managed:
            foreign += [prefix / part for part in PARTS[1:] if (prefix / part).exists()]
        if foreign:
            raise FastNvimError(
                "not installed by fast-nvim-release, so not overwritten:\n  "
                + "\n  ".join(str(path) for path in foreign)
                + "\n  re-run with --force to replace them, or pick another --prefix"
            )

    # claim everything first, so an interrupted install can be re-run without --force
    _write_marker(prefix, owned | set(names))
    for part in PARTS[1:]:
        _remove(prefix / part)
        if (source / part).is_dir():
            (prefix / part).parent.mkdir(parents=True, exist_ok=True)
            _copy(source / part, prefix / part)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for name in sorted(owned | set(names)):
        _remove(prefix / "bin" / name)
    for name in names:
        _copy(source / "bin" / name, prefix / "bin" / name)

    _write_marker(prefix, names)
    for old in (prefix / MARKER_DIR).glob("*.json"):  # markers from before 0.4
        if old.name != MARKER.name:
            old.unlink()
    return prefix / "bin" / exe.name


#: ``-i NONE`` keeps Neovim from reading and rewriting the user's shada file.
PROBE_ARGS = ("-u", "NONE", "-i", "NONE", "--headless", "+lua io.write(vim.env.VIMRUNTIME)", "+q")


def verify(exe: Path, prefix: Path) -> None:
    """Start the installed Neovim and check that it found the prefix's runtime."""
    # $VIMRUNTIME / $VIM would override where Neovim looks for its runtime
    env = {k: v for k, v in os.environ.items() if k not in ("VIMRUNTIME", "VIM")}
    try:
        result = subprocess.run([str(exe), *PROBE_ARGS], capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FastNvimError(f"installed, but {exe} does not run: {exc}") from exc
    reported = result.stdout.decode("utf-8", "replace").strip()
    expected = (prefix / "share" / "nvim" / "runtime").resolve()
    if result.returncode != 0 or not reported or Path(reported).resolve() != expected:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise FastNvimError(
            f"installed, but {exe} did not start with the runtime in {expected}"
            f" (it reported {reported or 'nothing'!r}, exit {result.returncode})"
            + (f"\n{detail}" if detail else "")
        )
