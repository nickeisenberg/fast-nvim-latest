#!/usr/bin/env python3
"""Download, verify and unpack Neovim builds into the package for a wheel.

    python scripts/fetch_nvim.py                          # this machine's platform
    python scripts/fetch_nvim.py --platform windows-arm64 # another one (cross-build)

Every release in ``platforms.RELEASES`` that has a build for the platform is
unpacked into ``src/fast_nvim_release/_nvim/<release>/<platform>/``.  Archives
are cached in ``~/.cache/fast-nvim-release`` (``FAST_NVIM_RELEASE_CACHE_DIR``)
and checked against the pinned sha256 every time they are used.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release import __version__  # noqa: E402
from fast_nvim_release.bundle import PARTS  # noqa: E402
from fast_nvim_release.platforms import PLATFORMS, RELEASES, Platform, host_platform  # noqa: E402

DEST = ROOT / "src" / "fast_nvim_release" / "_nvim"
LICENSE_URL = "https://raw.githubusercontent.com/neovim/neovim/{}/LICENSE.txt"
USER_AGENT = f"fast-nvim-release-build/{__version__}"


def cache_dir() -> Path:
    override = os.environ.get("FAST_NVIM_RELEASE_CACHE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".cache" / "fast-nvim-release"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    os.replace(partial, dest)


def archive(release_key: str, platform: Platform) -> Path:
    """A cached, checksum-verified release archive."""
    release = RELEASES[release_key]
    expected = release.sha256[platform.key]
    path = cache_dir() / release.version / platform.asset
    if not path.is_file() or sha256(path) != expected:
        print(f"  downloading {release.url(platform.key)}", flush=True)
        download(release.url(platform.key), path)
        actual = sha256(path)
        if actual != expected:
            path.unlink()
            raise SystemExit(
                f"checksum mismatch for {release.version} {platform.asset}:\n"
                f"  pinned {expected}\n  got    {actual}"
            )
    return path


def unpack(source: Path, platform: Platform, dest: Path) -> None:
    """Unpack the parts Neovim needs at runtime into ``dest`` (replacing it)."""
    with tempfile.TemporaryDirectory() as tmp:
        if platform.asset.endswith(".zip"):
            with zipfile.ZipFile(source) as zf:
                zf.extractall(tmp)  # zipfile never writes outside tmp or creates links
        else:
            with tarfile.open(source) as tar:
                # "data" rejects absolute paths, links leaving tmp, devices, setuid bits
                tar.extractall(tmp, filter="data")
        root = Path(tmp) / platform.archive_dir
        if not (root / "bin" / platform.exe).is_file():
            raise SystemExit(f"{source.name} has no {platform.archive_dir}/bin/{platform.exe}")
        shutil.rmtree(dest, ignore_errors=True)
        for part in PARTS:
            if (root / part).is_dir():
                (dest / part).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root / part), str(dest / part))


def check_runs(exe: Path, version: str) -> None:
    result = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    first = (result.stdout.splitlines() or [""])[0]
    if result.returncode or version not in first:
        raise SystemExit(f"{exe} --version printed {first!r}, expected {version}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=sorted(PLATFORMS), help="default: this machine")
    parser.add_argument("--dest", type=Path, default=DEST, help=f"default: {DEST}")
    args = parser.parse_args(argv)
    if not hasattr(tarfile, "data_filter"):
        raise SystemExit("needs tarfile extraction filters (Python 3.12+, or 3.9.17+/3.10.12+)")

    here = host_platform()
    platform = PLATFORMS[args.platform] if args.platform else here
    if platform is None:
        raise SystemExit("cannot detect this platform; pass --platform")

    for release_key, release in RELEASES.items():
        if platform.key not in release.sha256:
            continue
        print(f"{release_key}/{platform.key} ({release.version})", flush=True)
        tree = args.dest / release_key / platform.key
        unpack(archive(release_key, platform), platform, tree)
        if platform == here:
            check_runs(tree / "bin" / platform.exe, release.version)
        license_file = args.dest / f"LICENSE.nvim-{release_key}.txt"
        if not license_file.is_file():
            download(LICENSE_URL.format(release.version), license_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
