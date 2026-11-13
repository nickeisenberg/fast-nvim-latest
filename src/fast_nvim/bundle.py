"""Locate, fetch and install the vendored Neovim build.

The wheel ships one or more complete Neovim trees under
``fast_nvim/_nvim/<target-key>/``::

    _nvim/linux-x86_64/bin/nvim
    _nvim/linux-x86_64/lib/nvim/parser/*.so
    _nvim/linux-x86_64/share/nvim/runtime/...

That layout matters: Neovim finds its runtime by walking up from the real
path of its own executable (``../share/nvim/runtime``), so the runtime has to
travel with the binary.  A bare ``nvim`` executable is *not* enough.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

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

MARKER_DIR = Path("share") / "fast-nvim"

USER_AGENT = "fast-nvim/{} (+https://pypi.org/project/fast-nvim/)"


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
    """Targets whose build is included in this installation of fast-nvim."""
    keys = []
    for key in TARGETS:
        if _exe_ok(bundle_exe(key)):
            keys.append(key)
    return keys


def has_bundled(target: Target) -> bool:
    return _exe_ok(bundle_exe(target.key))


def read_build_info() -> Dict[str, object]:
    """Contents of ``_nvim/BUILD_INFO.json`` (written by scripts/fetch_nvim.py)."""
    path = BUNDLE_ROOT / "BUILD_INFO.json"
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
    override = os.environ.get("FAST_NVIM_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "fast-nvim"


def cached_archive(target: Target) -> Path:
    return cache_root() / NVIM_VERSION / target.asset


def cached_tree(target: Target) -> Path:
    return cache_root() / NVIM_VERSION / target.key


def default_prefix() -> Path:
    override = os.environ.get("FAST_NVIM_PREFIX")
    if override:
        return Path(override).expanduser()
    if sys.platform in ("win32", "cygwin", "msys"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "fast-nvim"
    return Path.home() / ".local"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num < 1024 or unit == "GiB":
            return (
                "{:.1f} {}".format(num, unit) if unit != "B" else "{:.0f} B".format(num)
            )
        num /= 1024.0
    return "{:.1f} GiB".format(num)


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
    partial = dest.with_name(dest.name + ".part")
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
            "could not download {}\n  {}\n"
            "  (no network? pass --offline to use a previously downloaded copy)".format(
                target.url, exc
            )
        ) from exc

    actual = digest.hexdigest()
    if target.sha256 and actual != target.sha256:
        partial.unlink(missing_ok=True)
        raise FastNvimError(
            "checksum mismatch for {}:\n  expected {}\n  got      {}".format(
                target.asset, target.sha256, actual
            )
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
            "{} is not available offline.\n"
            "  cached copies live in {} (nothing for {})".format(
                target.key, cache_root(), NVIM_VERSION
            )
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
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, out.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            unix_mode = (info.external_attr >> 16) & 0o777
            if unix_mode:
                os.chmod(out, unix_mode | stat.S_IRUSR | stat.S_IWUSR)
            count += 1
    return count


def extract_archive(target: Target, archive: Path, dest: Path) -> Path:
    """Unpack the parts of the release archive that Neovim needs at runtime."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".extract-", dir=str(dest.parent)))
    try:
        if target.archive == "zip":
            count = _extract_zip(archive, temp, target.strip_prefix)
        else:
            count = _extract_tar(archive, temp, target.strip_prefix)
        if count == 0:
            raise FastNvimError(
                "archive {} did not contain bin/, lib/nvim/ or share/nvim/runtime/".format(
                    archive.name
                )
            )
        os.replace(temp, dest)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    exe = dest / "bin" / target.exe
    if not exe.is_file():
        raise FastNvimError("archive {} has no bin/{}".format(archive.name, target.exe))
    ensure_executable(exe)
    return dest


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
    archive = fetch_archive(target, offline=offline, progress=progress)
    tree.parent.mkdir(parents=True, exist_ok=True)
    return extract_archive(target, archive, tree), "download"


# --------------------------------------------------------------------------
# installing into a prefix (what ``fast-nvim setup`` does)
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
    return Path(prefix) / MARKER_DIR / "{}.json".format(key)


def is_managed(prefix: Path, key: str) -> bool:
    return marker_file(prefix, key).is_file()


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


def install(
    target: Target,
    bundle: Path,
    prefix: Path,
    *,
    link: bool = False,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Install a Neovim tree into ``prefix`` (``bin/nvim`` + runtime)."""
    prefix = Path(prefix).expanduser()
    files = 0
    written: List[Path] = []
    for part, destination in BUNDLE_PARTS:
        source = Path(bundle) / part
        if not source.exists():
            continue
        dest = prefix / destination
        written.append(dest)
        if dry_run:
            continue
        if link:
            if part == "bin":
                # link executables/DLLs one by one so prefix/bin keeps its
                # other contents
                dest.mkdir(parents=True, exist_ok=True)
                for entry in sorted(source.iterdir()):
                    if entry.is_dir():
                        files += _link_tree(entry, dest / entry.name)
                    else:
                        files += _link_file(entry, dest / entry.name)
            else:
                files += _link_tree(source, dest)
        else:
            files += _copy_tree(source, dest)

    exe = prefix / "bin" / target.exe
    if not dry_run:
        ensure_executable(exe)
        marker = marker_file(prefix, target.key)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "target": target.key,
                    "nvim_version": NVIM_VERSION,
                    "bundle": str(bundle),
                    "link": link,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "exe": str(exe),
                    "files": files,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    size = 0
    for part, _ in BUNDLE_PARTS:
        source = Path(bundle) / part
        if not source.exists():
            continue
        for entry in source.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    size += entry.stat().st_size
                except OSError:
                    pass
    return {
        "prefix": prefix,
        "exe": exe,
        "files": files,
        "bytes": size,
        "paths": written,
        "linked": link,
    }


def neovim_version(exe: Path, timeout: float = 60.0) -> str:
    """Ask the binary itself what version it is."""
    import subprocess

    try:
        result = subprocess.run(
            [str(exe), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FastNvimError("could not run {}: {}".format(exe, exc)) from exc
    if result.returncode != 0:
        raise FastNvimError(
            "{} --version exited with {}\n{}".format(
                exe, result.returncode, result.stdout.decode("utf-8", "replace")
            )
        )
    first = result.stdout.decode("utf-8", "replace").splitlines() or [""]
    return first[0].strip()
