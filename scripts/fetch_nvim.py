#!/usr/bin/env python3
"""Download, verify and unpack the Neovim build for one wheel.

    python scripts/fetch_nvim.py                      # newest line, this machine
    python scripts/fetch_nvim.py --line 0.11          # another line
    python scripts/fetch_nvim.py --line 0.11 --wheel win_arm64   # cross-build

The build is unpacked into ``src/fast_nvim_release/_nvim/`` (replacing
whatever was there) together with ``build.json``, which ``setup.py`` reads for
the package version and wheel tag.  Archives are cached in
``~/.cache/fast-nvim-release`` (``FAST_NVIM_RELEASE_CACHE_DIR``) and checked
against the sha256 pinned in ``lines.json`` every time they are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import lines
from lines import Build, Wheel

from fast_nvim_release.bundle import PARTS
from fast_nvim_release.platforms import Platform, host_platform, host_wheel_tag

DEST = lines.ROOT / "src" / "fast_nvim_release" / "_nvim"
LICENSE_URL = "https://raw.githubusercontent.com/neovim/neovim/{}/LICENSE.txt"
#: files in ``DEST`` that are tracked in git, not produced by this script
TRACKED = (".gitkeep",)


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
    request = urllib.request.Request(url, headers={"User-Agent": "fast-nvim-release-build"})
    partial = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    os.replace(partial, dest)


def archive(build: Build, platform: Platform) -> Path:
    """A cached, checksum-verified release archive."""
    expected = build.sha256[platform.key]
    # keyed by checksum: both Neovim repositories publish e.g. v0.11.7/nvim-linux-x86_64.tar.gz
    path = cache_dir() / expected[:16] / platform.asset
    if not path.is_file() or sha256(path) != expected:
        print(f"  downloading {build.url(platform.key)}", flush=True)
        download(build.url(platform.key), path)
        actual = sha256(path)
        if actual != expected:
            path.unlink()
            raise SystemExit(
                f"checksum mismatch for {build.version} {platform.asset}:\n"
                f"  pinned {expected}\n  got    {actual}"
            )
    return path


def clear(dest: Path) -> None:
    """Remove a previous build from ``dest``, keeping the tracked files."""
    if not dest.is_dir():
        return
    for entry in dest.iterdir():
        if entry.name in TRACKED or entry.name.startswith("LICENSE.nvim-"):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def unpack(source: Path, platform: Platform, dest: Path) -> None:
    """Unpack the parts Neovim needs at runtime into ``dest``."""
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
        for part in PARTS:
            if (root / part).is_dir():
                (dest / part).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root / part), str(dest / part))


def check_runs(exe: Path, version: str) -> None:
    result = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    first = (result.stdout.splitlines() or [""])[0]
    if result.returncode or version not in first:
        reason = result.stderr.strip() or f"it printed {first!r}"
        raise SystemExit(
            f"{exe} does not run here (expected {version}):\n  {reason}\n"
            "  building for another kind of machine? pass --wheel (see --help)"
        )


def runs_here(wheel: Wheel) -> bool:
    """True if this machine can start the build (so it is worth a test run)."""
    here = host_platform()
    if here is None or here.key != wheel.platform.key:
        return False
    return wheel.build.kind == "old_glibc" or wheel.tag == host_wheel_tag()


def fetch(wheel: Wheel, dest: Path = DEST) -> None:
    print(f"{wheel.version}: Neovim {wheel.build.version} ({wheel.build.repo}) for {wheel.tag}")
    source = archive(wheel.build, wheel.platform)
    dest.mkdir(parents=True, exist_ok=True)
    clear(dest)
    unpack(source, wheel.platform, dest)
    info = {
        "version": wheel.version,
        "line": wheel.line,
        "neovim": wheel.build.version,
        "platform": wheel.platform.key,
        "tag": wheel.tag,
    }
    (dest / "build.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    if runs_here(wheel):
        check_runs(dest / "bin" / wheel.platform.exe, wheel.build.version)
    license_file = dest / f"LICENSE.nvim-{wheel.line}.txt"
    if not license_file.is_file():
        official = lines.load()[wheel.line]["official"]["version"]
        download(LICENSE_URL.format(official), license_file)


def main(argv=None) -> int:
    data = lines.load()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--line", choices=list(data), default=lines.newest_line(data))
    parser.add_argument("--wheel", help="wheel platform tag (default: the one pip picks here)")
    parser.add_argument("--dest", type=Path, default=DEST, help=f"default: {DEST}")
    args = parser.parse_args(argv)
    if not hasattr(tarfile, "data_filter"):
        raise SystemExit("needs tarfile extraction filters (Python 3.12+, or 3.9.17+/3.10.12+)")
    tag = args.wheel or host_wheel_tag()
    if tag is None:
        raise SystemExit("cannot detect this platform; pass --wheel")
    try:
        wheel = lines.find(args.line, tag, data)
    except KeyError as exc:
        raise SystemExit(exc.args[0]) from None
    fetch(wheel, args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
