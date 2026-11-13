"""Locate, fetch and install the vendored Neovim builds.

The wheel ships one complete Neovim tree per (release, platform) under
``fast_nvim_release/_nvim/<release>/<platform>/``::

    _nvim/0.12/linux-x86_64/bin/nvim
    _nvim/0.12/linux-x86_64/lib/nvim/parser/*.so
    _nvim/0.12/linux-x86_64/share/nvim/runtime/...
    _nvim/0.10/linux-x86_64/...            (older lines travel too)

That layout matters: Neovim finds its runtime by walking up from the real
path of its own executable (``../share/nvim/runtime``), so the runtime has to
travel with the binary.  A bare ``nvim`` executable is *not* enough.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Union

from . import __version__
from .platforms import (
    RELEASE_KEYS,
    RELEASES,
    Target,
)

PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = PACKAGE_DIR / "_nvim"

#: Directories copied out of the Neovim release archive, in prefix-relative
#: form: (archive-relative dir, install prefix-relative dir).
BUNDLE_PARTS: Tuple[Tuple[str, str], ...] = (
    ("bin", "bin"),
    ("lib/nvim", "lib/nvim"),
    ("share/nvim/runtime", "share/nvim/runtime"),
)

MARKER_DIR = Path("share") / "fast-nvim-release"

USER_AGENT = f"fast-nvim-release/{__version__} (+https://pypi.org/project/fast-nvim-release/)"

#: How long an install may hold the prefix lock before it is assumed to be a
#: leftover from a killed process (seconds).
LOCK_TIMEOUT = 60.0
LOCK_STALE_AFTER = 3600.0


class FastNvimError(RuntimeError):
    """Something the user can fix (bad platform, no network, unsafe archive)."""


# --------------------------------------------------------------------------
# vendored bundles (inside the installed wheel)
# --------------------------------------------------------------------------
def _exe_ok(path: Path) -> bool:
    return path.is_file()


def bundle_path(release_key: str, platform_key: str) -> Path:
    return BUNDLE_ROOT / release_key / platform_key


def bundle_exe(release_key: str, platform_key: str) -> Path:
    target = RELEASES[release_key].target(platform_key)
    if target is None:
        raise FastNvimError(f"Neovim {RELEASES[release_key].version} has no {platform_key} build")
    return bundle_path(release_key, platform_key) / "bin" / target.exe


def vendored_platforms(release_key: str) -> List[str]:
    """Platforms of ``release_key`` shipped inside this wheel.

    A build a release lists as ``downloaded_not_bundled`` never counts, even
    if a stray tree exists on disk: that list exists because shipping it would
    mis-tag the wheel.
    """
    release = RELEASES[release_key]
    return [
        target.key
        for target in release.targets
        if release.bundled(target.key) and _exe_ok(bundle_exe(release_key, target.key))
    ]


def vendored_releases() -> List[str]:
    """Releases with at least one build inside this wheel, oldest first."""
    return [key for key in RELEASE_KEYS if vendored_platforms(key)]


def vendored_targets() -> List[Target]:
    """Every (release, platform) build included in this installation."""
    found = []
    for key in RELEASE_KEYS:
        for platform_key in vendored_platforms(key):
            target = RELEASES[key].target(platform_key)
            if target is not None:
                found.append(target)
    return found


def has_bundled(target: Target) -> bool:
    """True if ``target`` ships in this wheel (and not merely downloadable)."""
    if not target.release.bundled(target.key):
        return False
    return _exe_ok(bundle_exe(target.release_key, target.key))


def _read_json(path: Path) -> Dict[str, object]:
    """A JSON object from ``path``, or ``{}`` if it is missing or malformed.

    The wheels carry a ``BUILD_INFO.json`` per build (provenance for
    ``scripts/check_wheels.py`` and for anyone reading the wheel), but nothing
    at runtime depends on it: a hand-assembled tree works without one.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# cache + downloads (for platforms that are not in the wheel)
# --------------------------------------------------------------------------
def normalize(path: Union[str, os.PathLike]) -> Path:
    """Absolute, ``~``-expanded form of a user supplied path.

    ``setup --prefix ./here`` must mean the same directory no matter where a
    later command is run from, and the paths we print and record in markers
    have to be usable as-is.
    """
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def cache_root() -> Path:
    override = os.environ.get("FAST_NVIM_RELEASE_CACHE_DIR")
    if override:
        return normalize(override)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return normalize(base / "fast-nvim-release")


def cached_archive(target: Target) -> Path:
    # the Neovim version is unique per release line, so it names the folder
    return cache_root() / target.nvim_version / target.asset


def cached_tree(target: Target) -> Path:
    return cache_root() / target.nvim_version / target.release_key / target.key


def default_prefix() -> Path:
    """Where ``path`` and ``version`` look when no ``--prefix`` is given.

    Only the read-only commands have a default: ``setup`` always needs an
    explicit prefix, so nothing is installed into a location the user did not
    name.
    """
    if sys.platform in ("win32", "cygwin", "msys"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return normalize(root / "fast-nvim-release")
    return Path.home() / ".local"


def origin(target: Target) -> Tuple[str, Optional[Path]]:
    """Where ``target`` would come from, without fetching anything.

    The one place that decides this: ``setup`` prints it, ``--list`` reports it
    and ``needs_download`` asks it whether to mention a download.

    :returns: ``("pip wheel", path)`` for a bundled build, ``("cache", tree)``,
        ``("cached archive", path)``, or ``("download", None)``.
    """
    if has_bundled(target):
        return "pip wheel", bundle_path(target.release_key, target.key)
    tree = cached_tree(target)
    if _exe_ok(tree / "bin" / target.exe):
        return "cache", tree
    archive = cached_archive(target)
    if archive.is_file():
        return "cached archive", archive
    return "download", None


def needs_download(target: Target) -> bool:
    """True if ``target`` is neither bundled nor unpacked/cached locally."""
    return origin(target)[0] == "download"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{num:.0f} B"
        num /= 1024.0
    return f"{num:.1f} GiB"


def human_size(num: float) -> str:
    return _human_size(num)


def _pinned(target: Target) -> str:
    """The sha256 pinned for ``target``; refusing to fetch without one.

    An empty pin used to mean "skip verification".  A checksum that silently
    disappears (a bad edit to RELEASES) must fail loudly instead of turning
    every download into an unverified one.
    """
    if not target.sha256:
        raise FastNvimError(
            f"{target.label} has no pinned sha256; refusing to use an unverified archive\n"
            "  pin it in src/fast_nvim_release/platforms.py (RELEASES)"
        )
    return target.sha256


def _download(
    target: Target,
    dest: Path,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Download ``target``'s archive to ``dest``, verifying its sha256."""
    expected = _pinned(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # unique per process, so two concurrent downloads cannot interleave
    partial = dest.with_name(f"{dest.name}.part-{os.getpid()}")
    request = urllib.request.Request(
        target.url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    digest = hashlib.sha256()
    seen = 0
    # The pinned size is the most we will ever write: a server that keeps
    # sending must not fill the cache directory before the checksum is checked.
    limit = target.size if target.size > 0 else None
    try:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or target.size)
                with partial.open("wb") as out:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        digest.update(chunk)
                        seen += len(chunk)
                        out.write(chunk)
                        if limit is not None and seen > limit:
                            raise FastNvimError(
                                f"{target.asset} is larger than the {target.size} bytes pinned\n"
                                "  in platforms.py; aborting the download"
                            )
                        if progress:
                            progress(seen, total)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise FastNvimError(
                f"could not download {target.url}\n  {exc}\n"
                "  (no network? pass --offline to use a previously downloaded copy)"
            ) from exc
    except BaseException:
        partial.unlink(missing_ok=True)  # never leave a half-written archive behind
        raise

    actual = digest.hexdigest()
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise FastNvimError(
            f"checksum mismatch for {target.asset}:\n  expected {expected}\n  got      {actual}"
        )
    os.replace(partial, dest)


def fetch_archive(
    target: Target,
    *,
    offline: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Return a checksum-verified local archive for ``target``."""
    expected = _pinned(target)
    archive = cached_archive(target)
    if archive.is_file():
        if _sha256(archive) == expected:
            return archive
        archive.unlink()  # stale/corrupt cache entry
    if offline:
        raise FastNvimError(
            f"Neovim {target.release.display} for {target.key} is not available offline.\n"
            f"  no usable cached copy in {cache_root()}"
        )
    _download(target, archive, progress=progress)
    return archive


# --------------------------------------------------------------------------
# archives -> Neovim tree
# --------------------------------------------------------------------------
def _keep(relative: str) -> bool:
    """True for the archive directories :data:`BUNDLE_PARTS` asks for.

    Derived from that table so adding a part cannot leave extraction silently
    ignoring it.
    """
    for part, _destination in BUNDLE_PARTS:
        if relative == part or relative.startswith(part + "/"):
            return True
    return False


def _safe_relpath(name: str) -> Optional[str]:
    relative = name.replace("\\", "/").lstrip("/")
    pieces = [piece for piece in relative.split("/") if piece not in ("", ".")]
    if not pieces or ".." in pieces:
        return None
    return "/".join(pieces)


def _strip(relative: str, prefix: str) -> str:
    if prefix and relative.startswith(prefix + "/"):
        return relative[len(prefix) + 1 :]
    return relative


def _safe_link_target(relative: str, linkname: str) -> Optional[str]:
    """Validate a symlink from ``relative``; ``None`` if it escapes ``dest``."""
    if not linkname or os.path.isabs(linkname):
        return None
    base = os.path.dirname(relative)
    resolved = os.path.normpath(os.path.join(base, linkname))
    if resolved == ".." or resolved.startswith(".." + os.sep):
        return None
    return linkname


def _extract_tar(archive: Path, dest: Path, strip_prefix: str) -> int:
    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            relative = _safe_relpath(member.name)
            if relative is None:
                continue
            relative = _strip(relative, strip_prefix)
            if relative is None or not _keep(relative):
                continue
            out = dest / relative
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym():
                if _safe_link_target(relative, member.linkname) is None:
                    raise FastNvimError(
                        f"archive {archive.name} contains an unsafe symlink: "
                        f"{member.name} -> {member.linkname}"
                    )
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.is_symlink() or out.exists():
                    out.unlink()
                os.symlink(member.linkname, out)
                count += 1
                continue
            if not member.isfile():
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with source, out.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            mode = member.mode & 0o777
            if mode:
                os.chmod(out, mode | stat.S_IRUSR | stat.S_IWUSR)
            count += 1
    return count


def _extract_zip(archive: Path, dest: Path, strip_prefix: str) -> int:
    count = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            relative = _safe_relpath(info.filename)
            if relative is None:
                continue
            relative = _strip(relative, strip_prefix)
            if relative is None or not _keep(relative):
                continue
            out = dest / relative
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            unix_mode = (info.external_attr >> 16) & 0o777
            if stat.S_ISLNK((info.external_attr >> 16) & 0o170000):
                raise FastNvimError(
                    f"archive {archive.name} contains a symlink "
                    f"({info.filename}); refusing to unpack it"
                )
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, out.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            if unix_mode:
                os.chmod(out, unix_mode | stat.S_IRUSR | stat.S_IWUSR)
            count += 1
    return count


def extract_archive(target: Target, archive: Path, dest: Path) -> Path:
    """Unpack the parts of the release archive that Neovim needs at runtime.

    The tree is unpacked into a temporary directory next to ``dest`` and only
    swapped in once it is complete, so a corrupt archive (or a crash) cannot
    leave a half-written tree or destroy the previous good one.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = dest.with_name(f"{dest.name}.old")
    if backup.exists() and not dest.exists():
        # a previous swap was killed between "move the old tree away" and
        # "move the new one in": put the old tree back and swap normally
        os.replace(backup, dest)
    temp = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(dest.parent)))
    swapped: Optional[Path] = None
    try:
        if target.archive == "zip":
            count = _extract_zip(archive, temp, target.strip_prefix)
        else:
            count = _extract_tar(archive, temp, target.strip_prefix)
        if count == 0:
            raise FastNvimError(
                f"archive {archive.name} did not contain bin/, lib/nvim/ or share/nvim/runtime/"
            )
        exe = temp / "bin" / target.exe
        if not exe.is_file():
            raise FastNvimError(f"archive {archive.name} has no bin/{target.exe}")
        ensure_executable(exe)
        if dest.exists():
            swapped = backup
            if swapped.exists():  # pragma: no cover - left by a killed run
                shutil.rmtree(swapped, ignore_errors=True)
            os.replace(dest, swapped)
        os.replace(temp, dest)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        if swapped is not None and swapped.exists() and not dest.exists():
            os.replace(swapped, dest)  # keep the previous tree usable
        raise
    if swapped is not None:
        shutil.rmtree(swapped, ignore_errors=True)
    return dest


# --------------------------------------------------------------------------
# tree swap leftovers
# --------------------------------------------------------------------------
def remove_stale_backups(directory: Path) -> None:
    """Delete ``<platform>.old`` trees left behind by a killed extraction.

    A tree lives at ``<root>/<release>/<platform>``, so its backup sits one
    level *below* the root this is given (``<root>/<release>/<platform>.old``).
    The root itself is swept too: a pre-rename layout, or a hand-assembled
    tree, would have put it there.
    """
    if not directory.is_dir():
        return
    for backup in list(directory.glob("*.old")) + list(directory.glob("*/*.old")):
        if backup.is_dir() and not backup.is_symlink():
            shutil.rmtree(backup, ignore_errors=True)


def ensure_bundle(
    target: Target,
    *,
    offline: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Path, str]:
    """Return ``(bundle_dir, where_from)`` for ``target``.

    Order of preference: the copy shipped inside the wheel, then the cache,
    then a download from GitHub.
    """
    if has_bundled(target):
        return bundle_path(target.release_key, target.key), "pip wheel"
    tree = cached_tree(target)
    if _exe_ok(tree / "bin" / target.exe):
        return tree, "cache"
    had_archive = cached_archive(target).is_file()
    archive = fetch_archive(target, offline=offline, progress=progress)
    tree.parent.mkdir(parents=True, exist_ok=True)
    # two `setup` runs (or a setup and a `run`) may want the same cache entry;
    # extract_archive swaps a shared <tree>.old aside, so let one at a time in
    with _lock(cache_lock(tree)):
        if _exe_ok(tree / "bin" / target.exe):  # the other process just finished
            return tree, "cache"
        return extract_archive(target, archive, tree), "cache" if had_archive else "download"


def cache_lock(tree: Path) -> Path:
    """The lock file that serializes extraction into one cached ``tree``."""
    return Path(tree).parent / f".{Path(tree).name}.lock"


# --------------------------------------------------------------------------
# installing into a prefix (what ``fast-nvim-release setup`` does)
# --------------------------------------------------------------------------
def ensure_executable(path: Path) -> None:
    """Make a file executable for the current user.

    Wheels are zips and pip does not restore the executable bit on package
    data, so the vendored ``nvim`` usually lands on disk as 0644.
    """
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    wanted = (
        stat.S_IRWXU
        | ((mode & stat.S_IRGRP) and stat.S_IXGRP)
        | ((mode & stat.S_IROTH) and stat.S_IXOTH)
    )
    if mode & stat.S_IXUSR == 0:
        try:
            os.chmod(path, mode | stat.S_IXUSR | wanted)
        except OSError:  # read-only install (e.g. a system site-packages)
            return


def marker_file(prefix: Path, release_key: str, platform_key: str) -> Path:
    return Path(prefix) / MARKER_DIR / f"{release_key}-{platform_key}.json"


def marker_info(prefix: Path, release_key: str, platform_key: str) -> Dict[str, object]:
    """The marker of a previous install, or ``{}`` when there is none."""
    return _read_json(marker_file(prefix, release_key, platform_key))


def is_managed(prefix: Path, release_key: str, platform_key: str) -> bool:
    """True if fast-nvim-release wrote (or started writing) this prefix."""
    return marker_file(prefix, release_key, platform_key).is_file()


def is_complete(prefix: Path, release_key: str, platform_key: str) -> bool:
    """True if a fast-nvim-release install finished (marker + executable present)."""
    if not is_managed(prefix, release_key, platform_key):
        return False
    info = marker_info(prefix, release_key, platform_key)
    if info.get("complete") is not True:  # killed mid-install or torn marker
        return False
    target = RELEASES[release_key].target(platform_key)
    if target is None:
        return False
    return _exe_ok(Path(prefix) / "bin" / target.exe)


def installed_builds(prefix: Path) -> List[Tuple[str, str]]:
    """``(release, platform)`` pairs fast-nvim-release installed into ``prefix``."""
    directory = Path(prefix) / MARKER_DIR
    if not directory.is_dir():
        return []
    found: List[Tuple[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        release_key, _, platform_key = path.stem.partition("-")
        if release_key in RELEASES and RELEASES[release_key].target(platform_key) is not None:
            found.append((release_key, platform_key))
    return found


def installed_targets(prefix: Path) -> List[Target]:
    """The builds fast-nvim-release *finished* installing into ``prefix``.

    A marker alone is not enough: ``install`` writes one before it copies
    anything, so a killed or failed install leaves a marker behind.  Reporting
    those here would contradict ``setup``'s refusal to call the prefix
    installed (``cmd_setup``) and ``path --installed-only``.
    """
    targets = []
    for release_key, platform_key in installed_builds(prefix):
        target = RELEASES[release_key].target(platform_key)
        if target is not None and is_complete(prefix, release_key, platform_key):
            targets.append(target)
    return targets


def installed_releases(prefix: Path) -> List[str]:
    """Release lines installed *and* finished in ``prefix``, oldest first."""
    return sorted({target.release_key for target in installed_targets(prefix)})


def _lock_is_stale(path: Path) -> bool:
    """True if nobody is holding ``path`` any more (killed process, old file).

    On Windows we cannot probe a pid (``os.kill(pid, 0)`` is not a no-op
    there), so a leftover lock is only taken over by age.  A waiter gives up
    after ``LOCK_TIMEOUT`` with a message naming the file, so the worst case is
    a 60-second stall followed by "remove it if that is not the case" -- not a
    wedged prefix.
    """
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:  # pragma: no cover - released in the meantime
        return True
    if age > LOCK_STALE_AFTER:
        return True
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process there
        return False
    try:
        pid = int(path.read_text(encoding="ascii").strip() or "0")
    except (OSError, ValueError):
        return False
    if pid <= 0:  # pragma: no cover - half-written lock
        return age > 5.0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:  # pragma: no cover - exists but belongs to another user
        return False
    return False


@contextlib.contextmanager
def _lock(path: Path, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Serialize installs into one prefix.

    A lock left behind by a killed process is detected by its pid (or, as a
    last resort, by age) so a stuck install cannot wedge the prefix forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if _lock_is_stale(path):
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise FastNvimError(
                    f"another fast-nvim-release process is holding {path} "
                    "(remove it if that is not the case)"
                ) from None
            time.sleep(0.1)
    try:
        try:
            os.write(handle, f"{os.getpid()}\n".encode("ascii"))
        finally:
            # close first: a failed write (a full disk, a quota) must not leak
            # the descriptor
            os.close(handle)
        yield
    finally:
        path.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_tree(source: Path, dest: Path) -> int:
    """Copy ``source`` onto ``dest`` and return the number of files written."""
    count = 0
    if dest.is_symlink():
        dest.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.rglob("*")):
        relative = entry.relative_to(source)
        out = dest / relative
        if entry.is_dir() and not entry.is_symlink():
            if out.is_symlink():
                out.unlink()  # left over from a previous --link install
            out.mkdir(parents=True, exist_ok=True)
            continue
        if out.is_symlink() or out.exists():
            _remove(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_symlink():
            os.symlink(os.readlink(entry), out)
        else:
            shutil.copy2(entry, out)
        count += 1
    return count


def _link_tree(source: Path, dest: Path) -> int:
    """Symlink ``source`` at ``dest`` (single link for a directory)."""
    if dest.is_symlink() and Path(os.readlink(dest)) == source:
        return 0
    if dest.is_symlink() or dest.exists():
        _remove(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(source, dest, target_is_directory=True)
    return 1


def _link_file(source: Path, dest: Path) -> int:
    if dest.is_symlink() and Path(os.readlink(dest)) == source:
        return 0
    if dest.is_symlink() or dest.exists():
        _remove(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(source, dest)
    return 1


def _tree_size(bundle_dir: Path) -> int:
    size = 0
    for part, _ in BUNDLE_PARTS:
        source = Path(bundle_dir) / part
        if not source.exists():
            continue
        for entry in source.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    size += entry.stat().st_size
                except OSError:
                    pass
    return size


def _write_marker(
    prefix: Path,
    target: Target,
    bundle_dir: Path,
    *,
    link: bool,
    entries: int,
    bin_names: List[str],
    complete: bool,
) -> None:
    marker = marker_file(prefix, target.release_key, target.key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "target": target.key,
                "release": target.release_key,
                "nvim_version": target.nvim_version,
                "bundle": str(bundle_dir),
                "link": link,
                "complete": complete,
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "exe": str(Path(prefix) / "bin" / target.exe),
                "entries": entries,
                "bin": bin_names,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _plain_name(name: object) -> bool:
    """True for a bare file name that is safe to join onto ``bin``."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
    )


def _marker_bin_names(info: Dict[str, object]) -> List[str]:
    """The bin entries a marker claims, ignoring anything malformed.

    A marker is a file users can edit, and ``"bin": "nvim"`` (a string) would
    otherwise be iterated character by character.
    """
    names = info.get("bin")
    if not isinstance(names, list):
        return []
    return [name for name in names if _plain_name(name)]


def _drop_other_installs(prefix: Path, keep: Tuple[str, str]) -> List[str]:
    """Forget other builds' markers and report bin entries they owned.

    A prefix holds exactly one Neovim, so installing another release (or
    another platform's build) must remove the previous marker and the
    executables it put in ``bin`` -- otherwise ``path`` keeps claiming a build
    that is gone and ``bin`` ends up holding two Neovims.
    """
    stale: List[str] = []
    for release_key, platform_key in installed_builds(prefix):
        if (release_key, platform_key) == keep:
            continue
        for name in _marker_bin_names(marker_info(prefix, release_key, platform_key)):
            stale.append(name)
        marker_file(prefix, release_key, platform_key).unlink(missing_ok=True)
    return stale


def _stage_parts(bundle_dir: Path, staging: Path, *, link: bool) -> Tuple[int, int]:
    """Copy or link every part of ``bundle_dir`` into ``staging``.

    :returns: ``(files copied, links made)``.
    """
    files = 0
    links = 0
    for part, destination in BUNDLE_PARTS:
        source = bundle_dir / part
        if not source.exists():
            continue
        staged = staging / destination
        if link:
            if part == "bin":
                # link executables/DLLs one by one so the merge into prefix/bin
                # can leave that directory's other contents alone
                staged.mkdir(parents=True, exist_ok=True)
                for entry in sorted(source.iterdir()):
                    if entry.is_dir():
                        links += _link_tree(entry, staged / entry.name)
                    else:
                        links += _link_file(entry, staged / entry.name)
            else:
                links += _link_tree(source, staged)
        else:
            files += _copy_tree(source, staged)
    return files, links


def _swap_parts(staging: Path, prefix: Path, *, merge_bin: bool) -> None:
    """Move the staged parts into ``prefix``.

    ``bin`` is merged, because a prefix's ``bin`` usually holds other tools;
    everything else is replaced as a whole.  Only renames happen here, so the
    window where a prefix holds a half-updated tree is as short as it can be.
    """
    for _part, destination in BUNDLE_PARTS:
        staged = staging / destination
        target = prefix / destination
        if not staged.exists() and not staged.is_symlink():
            continue
        if merge_bin and destination == "bin":
            target.mkdir(parents=True, exist_ok=True)
            for entry in sorted(staged.iterdir()):
                out = target / entry.name
                if out.is_symlink() or out.exists():
                    _remove(out)
                os.replace(entry, out)
        else:
            _remove(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)


def install(
    target: Target,
    bundle_dir: Path,
    prefix: Path,
    *,
    link: bool = False,
    replace: bool = False,
) -> Dict[str, object]:
    """Install a Neovim tree into ``prefix`` (``bin/nvim`` + runtime).

    Re-installing into a prefix fast-nvim-release already manages replaces the files
    it owns (``lib/nvim``, ``share/nvim/runtime`` and the entries it put in
    ``bin``) so an upgrade cannot leave a mix of two Neovim versions behind.
    Everything else in the prefix is left alone.  ``replace`` clears those
    directories even when the prefix has no marker yet (``setup --force``), and
    always forgets any other build in the same prefix: a prefix holds exactly
    one Neovim, so it is also how ``setup 0.10`` replaces ``setup 0.12``.

    The tree is staged inside the prefix and swapped in only once every file has
    been written, so a failure while copying (a full disk, an unreadable source
    file) leaves the previous install exactly as it was instead of a mix of the
    two -- the same guarantee ``extract_archive`` gives the cache.
    """
    prefix = normalize(prefix)
    bundle_dir = Path(bundle_dir)
    bin_source = bundle_dir / "bin"
    bin_names = sorted(entry.name for entry in bin_source.iterdir()) if bin_source.is_dir() else []
    previous = marker_info(prefix, target.release_key, target.key)
    files = 0
    links = 0
    written: List[Path] = []
    staging = prefix / MARKER_DIR / ".staging"

    with _lock(prefix / MARKER_DIR / ".lock"):
        managed = is_managed(prefix, target.release_key, target.key)
        # another release or platform in this prefix is replaced, not merged
        others = [
            build for build in installed_builds(prefix) if build != (target.release_key, target.key)
        ]
        # written before the copy: if this process dies half way, the next
        # `setup` recognises the prefix as its own instead of demanding --force
        _write_marker(
            prefix,
            target,
            bundle_dir,
            link=link,
            entries=0,
            bin_names=bin_names,
            complete=False,
        )
        try:
            _remove(staging)  # left behind by a killed install
            files, links = _stage_parts(bundle_dir, staging, link=link)
            for part, destination in BUNDLE_PARTS:
                if (bundle_dir / part).exists():
                    written.append(prefix / destination)
            _swap_parts(staging, prefix, merge_bin=True)
        finally:
            _remove(staging)

        # only now is the new build in place: forgetting the previous build (and
        # the bin entries it owned) can no longer strand the prefix
        stale_names = _drop_other_installs(prefix, (target.release_key, target.key))
        if managed or replace or others:
            for name in _marker_bin_names(previous) + stale_names:
                if name not in bin_names:
                    _remove(prefix / "bin" / name)

        exe = prefix / "bin" / target.exe
        if not exe.is_file():
            raise FastNvimError(f"{bundle_dir} did not provide bin/{target.exe}")
        ensure_executable(exe)
        _write_marker(
            prefix,
            target,
            bundle_dir,
            link=link,
            entries=files + links,
            bin_names=bin_names,
            complete=True,
        )

    return {
        "prefix": prefix,
        "exe": exe,
        "files": files,
        "links": links,
        "bytes": 0 if link else _tree_size(bundle_dir),
        "paths": written,
        "linked": link,
    }


def _run(exe: Path, args: List[str], timeout: float) -> subprocess.CompletedProcess:
    """Run ``exe`` with the two streams kept apart.

    Neovim can write warnings to stderr on a perfectly good start-up, and
    callers parse stdout (``--version``'s first line, the ``$VIMRUNTIME`` we
    asked for), so merging the streams would corrupt the answer.
    """
    try:
        return subprocess.run(
            [str(exe), *args],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FastNvimError(f"could not run {exe}: {exc}") from exc


def _output(result: subprocess.CompletedProcess) -> str:
    """Everything the process printed, for an error message."""
    parts = [
        stream.decode("utf-8", "replace").strip()
        for stream in (result.stdout, result.stderr)
        if stream
    ]
    return "\n".join(part for part in parts if part)


def neovim_version(exe: Path, timeout: float = 60.0) -> str:
    """Ask the binary itself what version it is."""
    result = _run(exe, ["--version"], timeout)
    if result.returncode != 0:
        raise FastNvimError(f"{exe} --version exited with {result.returncode}\n{_output(result)}")
    first = result.stdout.decode("utf-8", "replace").splitlines() or [""]
    return first[0].strip()


#: Arguments for the start-up probe that reads ``$VIMRUNTIME``.  ``-i NONE``
#: matters as much as ``-u NONE``: without it Neovim reads *and rewrites* the
#: user's shada file, so a plain ``setup`` would quietly touch their marks,
#: registers and history (and race with an editor they have open).
PROBE_ARGS: Tuple[str, ...] = (
    "-u",
    "NONE",
    "-i",
    "NONE",
    "--headless",
    "+lua io.write(vim.env.VIMRUNTIME)",
    "+q",
)


def runtime_path(exe: Path, timeout: float = 60.0) -> str:
    """Ask the binary which ``$VIMRUNTIME`` it resolved (start-up check)."""
    result = _run(exe, list(PROBE_ARGS), timeout)
    output = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0:
        raise FastNvimError(f"{exe} could not start (exit {result.returncode}):\n{_output(result)}")
    return output


def verify_install(exe: Path, prefix: Path, timeout: float = 60.0) -> str:
    """Run the installed binary and check it uses the prefix's runtime.

    ``nvim --version`` works even when the runtime is missing (it then falls
    back to an unrelated ``$VIMRUNTIME``), so the only honest check is to let
    Neovim resolve its runtime and see where it lands.
    """
    version = neovim_version(exe, timeout=timeout)
    reported = runtime_path(exe, timeout=timeout)
    expected = (Path(prefix) / "share" / "nvim" / "runtime").resolve()
    if not reported:
        raise FastNvimError(f"{exe} did not report a $VIMRUNTIME; expected {expected}")
    actual = Path(reported).resolve()
    if actual != expected:
        raise FastNvimError(
            f"{exe} uses {actual} as $VIMRUNTIME, expected {expected}\n"
            "  the runtime did not come along (re-run with --force)"
        )
    return version
