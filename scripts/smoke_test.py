#!/usr/bin/env python3
"""Install a built wheel into a clean venv and use it like a real user would.

    python scripts/smoke_test.py dist/fast_nvim_release-0.2.0-py3-none-manylinux_2_34_x86_64.whl

Checks ``version``, ``run``, ``setup`` of more than one release, and that the
installed nvim resolves its ``$VIMRUNTIME`` inside the prefix (i.e. the runtime
really came along).  Used by CI on Linux, macOS and Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import List, NoReturn, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release import __version__  # noqa: E402
from fast_nvim_release.platforms import host_platform  # noqa: E402


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 300):
    print("$ " + " ".join(str(part) for part in cmd), flush=True)
    return subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def fail(message: str, output: bytes = b"") -> NoReturn:
    print("FAILED: " + message, file=sys.stderr)
    if output:
        print(output.decode("utf-8", "replace"), file=sys.stderr)
    raise SystemExit(1)


def find_wheel(explicit: List[str]) -> Path:
    if explicit:
        return Path(explicit[0]).expanduser().resolve()
    here = host_platform()
    if here is None:
        fail("cannot detect this platform; pass a wheel path")
    candidates = sorted(
        (ROOT / "dist").glob(f"fast_nvim_release-*-{here.wheel_tag}.whl"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        fail(f"no wheel for {here.key} in dist/; build one with `python -m build --wheel`")
    current = [
        path for path in candidates if path.name.startswith(f"fast_nvim_release-{__version__}-")
    ]
    if current:
        return current[-1]
    print(
        f"warning: no {__version__} wheel in dist/, testing the newest: {candidates[-1].name}",
        file=sys.stderr,
    )
    return candidates[-1]


def runtime_of(exe: Path) -> str:
    result = run([exe, "-u", "NONE", "--headless", "+lua io.write(vim.env.VIMRUNTIME)", "+q"])
    return result.stdout.decode("utf-8", "replace").strip()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="*", help="wheel to test (default: host wheel in dist/)")
    args = parser.parse_args(argv)

    wheel = find_wheel(args.wheel)
    here = host_platform()
    if here is None:
        fail("unknown host platform")

    print(f"smoke testing {wheel.name} on {here.key}")
    work = Path(tempfile.mkdtemp(prefix="fast-nvim-release-smoke-"))
    venv_dir = work / "venv"
    prefix = work / "prefix"
    try:
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
        python = (
            venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"
        )
        console = (
            venv_dir / "Scripts" / "fast-nvim-release.exe"
            if os.name == "nt"
            else venv_dir / "bin" / "fast-nvim-release"
        )

        result = run([python, "-m", "pip", "install", "--no-deps", wheel])
        if result.returncode:
            fail("pip install failed", result.stdout)

        result = run([console, "version", "--json"])
        if result.returncode:
            fail("`fast-nvim-release version --json` failed", result.stdout)
        payload = json.loads(result.stdout)
        if not payload["bundled"]:
            fail("wheel does not bundle {}: {}".format(here.key, payload["vendored_targets"]))
        print("  version: {} / {}".format(payload["fast_nvim_release"], payload["nvim_version"]))
        bundled_targets = payload["vendored_targets"]
        print("  bundled: {}".format(", ".join(bundled_targets)))

        # `setup --list` must know every release we claim to support
        result = run([console, "setup", "--list"])
        if result.returncode:
            fail("`fast-nvim-release setup --list` failed", result.stdout)
        listing = result.stdout.decode("utf-8", "replace")
        for release_key in payload["releases"]:
            if release_key not in listing:
                fail(f"setup --list does not mention release {release_key}", result.stdout)

        # walk every release bundled for this platform, newest last
        installed_versions = {}
        for label in bundled_targets:
            release_key, platform_key = label.split("/")
            if platform_key != here.key:
                continue
            result = run([console, "run", release_key, "--", "--version"])
            if result.returncode:
                fail(f"`run {release_key}` failed", result.stdout)
            first = result.stdout.decode("utf-8", "replace").splitlines()[0]
            if not first.startswith("NVIM "):
                fail(f"run {release_key} printed {first!r}", result.stdout)
            print(f"  run {release_key}: {first}")

            result = run([console, "setup", release_key, "--prefix", prefix, "--quiet"])
            if result.returncode:
                fail(f"`setup {release_key}` failed", result.stdout)
            exe = prefix / "bin" / here.exe
            if not exe.is_file():
                fail(f"setup did not install {exe}", result.stdout)
            if os.name != "nt" and not os.access(exe, os.X_OK):
                fail(f"{exe} is not executable")
            if not (prefix / "share" / "nvim" / "runtime" / "syntax" / "syntax.vim").is_file():
                fail("runtime was not installed into the prefix", result.stdout)

            installed = runtime_of(exe)
            # resolve both sides: macOS turns /var/folders/... into /private/var/...
            expected_runtime = (prefix / "share" / "nvim" / "runtime").resolve()
            actual_runtime = Path(installed).resolve() if installed else None
            if actual_runtime != expected_runtime:
                fail(f"installed nvim uses {installed!r}, expected {expected_runtime}")
            print(f"  setup {release_key}: $VIMRUNTIME={installed}")

            result = run([exe, "--version"])
            first = result.stdout.decode("utf-8", "replace").splitlines()[0]
            if result.returncode or not first.startswith("NVIM "):
                fail("installed nvim --version failed", result.stdout)
            installed_versions[release_key] = first
            print(f"  {first}")

        if not installed_versions:
            fail("no bundled release could be installed on this platform")
        if len(set(installed_versions.values())) < len(installed_versions):
            fail(f"two releases installed the same Neovim version: {installed_versions}")

        print(f"\nPASS {wheel.name}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
