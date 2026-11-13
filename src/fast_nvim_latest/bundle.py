"""Locate, fetch and install the vendored Neovim build.

The wheel ships one or more complete Neovim trees under
``fast_nvim_latest/_nvim/<target-key>/``::

    _nvim/linux-x86_64/bin/nvim
    _nvim/linux-x86_64/lib/nvim/parser/*.so
    _nvim/linux-x86_64/share/nvim/runtime/...

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
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import __version__
from .platforms import NVIM_VERSION, TARGETS, Target

PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLE_ROOT = PACKAGE_DIR / "_nvim"

#: Directories copied out of the Neovim release archive, in prefix-relative
#: form: (archive-relative dir, install prefix-relative dir).
BUNDLE_PARTS: Tuple[Tuple[str, str], ...] = (
    ("bin", "bin"),
    ("lib/nvim", "lib/nvim"),
    ("share/nvim/runtime", "share/nvim/runtime"),
)

MARKER_DIR = Path("share") / "fast-nvim-latest"

USER_AGENT = f"fast-nvim-latest/{__version__} (+https://pypi.org/project/fast-nvim-latest/)"

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


def bundle_path(key: str) -> Path:
    return BUNDLE_ROOT / key


def bundle_exe(key: str) -> Path:
    target = TARGETS[key]
    return bundle_path(key) / "bin" / target.exe


def vendored_keys() -> List[str]:
    """Targets whose build is included in this installation of fast-nvim-latest."""
    keys = []
    for key in TARGETS:
        if _exe_ok(bundle_exe(key)):
            keys.append(key)
    return keys


def has_bundled(target: Target) -> bool:
    return _exe_ok(bundle_exe(target.key))


def read_build_info() -> Dict[str, object]:
    """Contents of ``_nvim/BUILD_INFO.json`` (written by scripts/fetch_nvim.py)."""
    return _read_json(BUNDLE_ROOT / "BUILD_INFO.json")


def bundle_build_info(key: str) -> Dict[str, object]:
    """Contents of ``_nvim/<key>/BUILD_INFO.json`` for one vendored build."""
    return _read_json(bundle_path(key) / "BUILD_INFO.json")


def _read_json(path: Path) -> Dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def bundled_nvim_version() -> Optional[str]:
    info = read_build_info()
    version = info.get("nvim_version")
    return version if isinstance(version, str) else None


# --------------------------------------------------------------------------
# cache + downloads (for platforms that are not in the wheel)
# --------------------------------------------------------------------------
def cache_root() -> Path:
    override = os.environ.get("FAST_NVIM_LATEST_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "fast-nvim-latest"


def cached_archive(target: Target) -> Path:
    return cache_root() / NVIM_VERSION / target.asset


def cached_tree(target: Target) -> Path:
    return cache_root() / NVIM_VERSION / target.key


def default_prefix() -> Path:
    override = os.environ.get("FAST_NVIM_LATEST_PREFIX")
    if override:
        return Path(override).expanduser()
    if sys.platform in ("win32", "cygwin", "msys"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "fast-nvim-latest"
    return Path.home() / ".local"


def needs_download(target: Target) -> bool:
    """True if ``target`` is neither bundled nor unpacked/cached locally."""
    if has_bundled(target):
        return False
    if _exe_ok(cached_tree(target) / "bin" / target.exe):
        return False
    return not cached_archive(target).is_file()


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


def _download(
    target: Target,
    dest: Path,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Download ``target``'s archive to ``dest``, verifying its sha256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # unique per process, so two concurrent downloads cannot interleave
    partial = dest.with_name(f"{dest.name}.part-{os.getpid()}")
    request = urllib.request.Request(
        target.url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    digest = hashlib.sha256()
    seen = 0
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
                    if progress:
                        progress(seen, total)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise FastNvimError(
            f"could not download {target.url}\n  {exc}\n"
            "  (no network? pass --offline to use a previously downloaded copy)"
        ) from exc

    actual = digest.hexdigest()
    if target.sha256 and actual != target.sha256:
        partial.unlink(missing_ok=True)
        raise FastNvimError(
            f"checksum mismatch for {target.asset}:\n"
            f"  expected {target.sha256}\n"
            f"  got      {actual}"
        )
    os.replace(partial, dest)


def fetch_archive(
    target: Target,
    *,
    offline: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Return a checksum-verified local archive for ``target``."""
    archive = cached_archive(target)
    if archive.is_file():
        if not target.sha256 or _sha256(archive) == target.sha256:
            return archive
        archive.unlink()  # stale/corrupt cache entry
    if offline:
        raise FastNvimError(
            f"{target.key} is not available offline.\n"
            f"  cached copies live in {cache_root()} (nothing for {NVIM_VERSION})"
        )
    _download(target, archive, progress=progress)
    return archive


# --------------------------------------------------------------------------
# archives -> neonvim tree
# --------------------------------------------------------------------------
def _keep(relative: str) -> bool:
    for part in ("bin", "lib/nvim", "share/nvim/runtime"):
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
    temp = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(dest.parent)))
    backup: Optional[Path] = None
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
            backup = dest.with_name(f"{dest.name}.old")
            if backup.exists():  # pragma: no cover - left by a killed run
                shutil.rmtree(backup, ignore_errors=True)
            os.replace(dest, backup)
        os.replace(temp, dest)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        if backup is not None and backup.exists() and not dest.exists():
            os.replace(backup, dest)  # keep the previous tree usable
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
    return dest


# --------------------------------------------------------------------------
# archive -> tree swap
# --------------------------------------------------------------------------
def remove_stale_backups(directory: Path) -> None:
    """Delete ``<target>.old`` trees left behind by a killed extraction."""
    if not directory.is_dir():
        return
    for backup in directory.glob("*.old"):
        if backup.is_dir():
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
        return bundle_path(target.key), "pip wheel"
    tree = cached_tree(target)
    if _exe_ok(tree / "bin" / target.exe):
        return tree, "cache"
    had_archive = cached_archive(target).is_file()
    archive = fetch_archive(target, offline=offline, progress=progress)
    tree.parent.mkdir(parents=True, exist_ok=True)
    return extract_archive(target, archive, tree), "cache" if had_archive else "download"


# --------------------------------------------------------------------------
# installing into a prefix (what ``fast-nvim-latest setup`` does)
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
        os.chmod(path, mode | stat.S_IXUSR | wanted)


def marker_file(prefix: Path, key: str) -> Path:
    return Path(prefix) / MARKER_DIR / f"{key}.json"


def marker_info(prefix: Path, key: str) -> Dict[str, object]:
    """The marker of a previous install, or ``{}`` when there is none."""
    return _read_json(marker_file(prefix, key))


def is_managed(prefix: Path, key: str) -> bool:
    """True if fast-nvim-latest wrote (or started writing) this prefix."""
    return marker_file(prefix, key).is_file()


def is_complete(prefix: Path, key: str) -> bool:
    """True if a fast-nvim-latest install finished (marker + executable present)."""
    if not is_managed(prefix, key):
        return False
    info = marker_info(prefix, key)
    if info.get("complete") is False:  # killed mid-install
        return False
    return _exe_ok(Path(prefix) / "bin" / TARGETS[key].exe)


def installed_targets(prefix: Path) -> List[str]:
    directory = Path(prefix) / MARKER_DIR
    if not directory.is_dir():
        return []
    keys = []
    for path in sorted(directory.glob("*.json")):
        key = path.stem
        if key in TARGETS:
            keys.append(key)
    return keys


def _lock_is_stale(path: Path) -> bool:
    """True if nobody is holding ``path`` any more (killed process, old file)."""
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
                    f"another fast-nvim-latest setup is running in {path.parent.parent} "
                    f"(lock file {path}); remove it if that is not the case"
                ) from None
            time.sleep(0.1)
    try:
        os.write(handle, f"{os.getpid()}\n".encode("ascii"))
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
    marker = marker_file(prefix, target.key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "target": target.key,
                "nvim_version": NVIM_VERSION,
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


def install(
    target: Target,
    bundle_dir: Path,
    prefix: Path,
    *,
    link: bool = False,
    replace: bool = False,
) -> Dict[str, object]:
    """Install a Neovim tree into ``prefix`` (``bin/nvim`` + runtime).

    Re-installing into a prefix fast-nvim-latest already manages replaces the files
    it owns (``lib/nvim``, ``share/nvim/runtime`` and the entries it put in
    ``bin``) so an upgrade cannot leave a mix of two Neovim versions behind.
    Everything else in the prefix is left alone.  ``replace`` clears those
    directories even when the prefix has no marker yet (``setup --force``).
    """
    prefix = Path(prefix).expanduser()
    bundle_dir = Path(bundle_dir)
    bin_source = bundle_dir / "bin"
    bin_names = sorted(entry.name for entry in bin_source.iterdir()) if bin_source.is_dir() else []
    previous = marker_info(prefix, target.key)
    files = 0
    links = 0
    written: List[Path] = []

    with _lock(prefix / MARKER_DIR / ".lock"):
        if previous or replace:
            for _, destination in BUNDLE_PARTS:
                if destination != "bin":
                    _remove(prefix / destination)
            for name in previous.get("bin") or []:
                if name not in bin_names:
                    _remove(prefix / "bin" / name)
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

        for part, destination in BUNDLE_PARTS:
            source = bundle_dir / part
            if not source.exists():
                continue
            dest = prefix / destination
            written.append(dest)
            if link:
                if part == "bin":
                    # link executables/DLLs one by one so prefix/bin keeps its
                    # other contents
                    dest.mkdir(parents=True, exist_ok=True)
                    for entry in sorted(source.iterdir()):
                        if entry.is_dir():
                            links += _link_tree(entry, dest / entry.name)
                        else:
                            links += _link_file(entry, dest / entry.name)
                else:
                    links += _link_tree(source, dest)
            else:
                files += _copy_tree(source, dest)

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
    try:
        return subprocess.run(
            [str(exe), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FastNvimError(f"could not run {exe}: {exc}") from exc


def neovim_version(exe: Path, timeout: float = 60.0) -> str:
    """Ask the binary itself what version it is."""
    result = _run(exe, ["--version"], timeout)
    if result.returncode != 0:
        raise FastNvimError(
            "{} --version exited with {}\n{}".format(
                exe, result.returncode, result.stdout.decode("utf-8", "replace")
            )
        )
    first = result.stdout.decode("utf-8", "replace").splitlines() or [""]
    return first[0].strip()


def runtime_path(exe: Path, timeout: float = 60.0) -> str:
    """Ask the binary which ``$VIMRUNTIME`` it resolved (start-up check)."""
    result = _run(
        exe,
        [
            "-u",
            "NONE",
            "--headless",
            "+lua io.write(vim.env.VIMRUNTIME)",
            "+q",
        ],
        timeout,
    )
    output = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0:
        raise FastNvimError(f"{exe} could not start (exit {result.returncode}):\n{output}")
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
