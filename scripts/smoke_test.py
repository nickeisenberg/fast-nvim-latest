#!/usr/bin/env python3
"""Install a built wheel into a clean venv and use it like a real user would.

    python scripts/smoke_test.py dist/fast_nvim_latest-0.1.0-py3-none-manylinux_2_34_x86_64.whl

Checks ``version``, ``run``, ``setup`` and that the installed nvim resolves its
``$VIMRUNTIME`` inside the prefix (i.e. the runtime really came along).
Used by CI on Linux, macOS and Windows.
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

from fast_nvim_latest import __version__  # noqa: E402
from fast_nvim_latest.platforms import host_target  # noqa: E402


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
    host = host_target()
    if host is None:
        fail("cannot detect this platform; pass a wheel path")
    candidates = sorted(
        (ROOT / "dist").glob(f"fast_nvim_latest-*-{host.wheel_tag}.whl"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        fail(f"no wheel for {host.key} in dist/; build one with `python -m build --wheel`")
    current = [
        path for path in candidates if path.name.startswith(f"fast_nvim_latest-{__version__}-")
    ]
    if current:
        return current[-1]
    print(
        f"warning: no {__version__} wheel in dist/, testing the newest: {candidates[-1].name}",
        file=sys.stderr,
    )
    return candidates[-1]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="*", help="wheel to test (default: host wheel in dist/)")
    args = parser.parse_args(argv)

    wheel = find_wheel(args.wheel)
    host = host_target()
    if host is None:
        fail("unknown host platform")

    print(f"smoke testing {wheel.name} on {host.key}")
    work = Path(tempfile.mkdtemp(prefix="fast-nvim-latest-smoke-"))
    venv_dir = work / "venv"
    prefix = work / "prefix"
    try:
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
        python = (
            venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"
        )
        console = (
            venv_dir / "Scripts" / "fast-nvim-latest.exe"
            if os.name == "nt"
            else venv_dir / "bin" / "fast-nvim-latest"
        )

        result = run([python, "-m", "pip", "install", "--no-deps", wheel])
        if result.returncode:
            fail("pip install failed", result.stdout)

        result = run([console, "version", "--json"])
        if result.returncode:
            fail("`fast-nvim-latest version --json` failed", result.stdout)
        payload = json.loads(result.stdout)
        if payload["vendored_targets"] != [host.key]:
            fail("wheel does not bundle {}: {}".format(host.key, payload["vendored_targets"]))
        print("  version: {} / {}".format(payload["fast_nvim_latest"], payload["nvim_release"]))

        # `run` executes the bundled binary and must find its runtime in site-packages
        result = run(
            [
                console,
                "run",
                "--",
                "-u",
                "NONE",
                "--headless",
                "+lua io.write(vim.env.VIMRUNTIME)",
                "+q",
            ]
        )
        runtime = result.stdout.decode("utf-8", "replace").strip()
        if result.returncode or "_nvim" not in runtime:
            fail(
                f"bundled nvim did not report a VIMRUNTIME: {runtime!r}",
                result.stdout,
            )
        print(f"  bundled VIMRUNTIME: {runtime}")

        # `setup` installs a self-contained prefix
        result = run([console, "setup", "--prefix", prefix, "--quiet"])
        if result.returncode:
            fail("`fast-nvim-latest setup` failed", result.stdout)
        exe = prefix / "bin" / host.exe
        if not exe.is_file():
            fail(f"setup did not install {exe}", result.stdout)
        if os.name != "nt" and not os.access(exe, os.X_OK):
            fail(f"{exe} is not executable")
        if not (prefix / "share" / "nvim" / "runtime" / "syntax" / "syntax.vim").is_file():
            fail("runtime was not installed into the prefix", result.stdout)

        result = run(
            [
                exe,
                "-u",
                "NONE",
                "--headless",
                "+lua io.write(vim.env.VIMRUNTIME)",
                "+q",
            ]
        )
        installed_runtime = result.stdout.decode("utf-8", "replace").strip()
        if result.returncode or Path(installed_runtime) != prefix / "share" / "nvim" / "runtime":
            fail(
                "installed nvim uses {!r}, expected {}".format(
                    installed_runtime, prefix / "share" / "nvim" / "runtime"
                ),
                result.stdout,
            )
        print(f"  installed VIMRUNTIME: {installed_runtime}")

        # and it should be on PATH-able terms with itself: `nvim --version`
        result = run([exe, "--version"])
        first = result.stdout.decode("utf-8", "replace").splitlines()[0]
        if result.returncode or not first.startswith("NVIM "):
            fail("installed nvim --version failed", result.stdout)
        print(f"  {first}")
        print(f"\nPASS {wheel.name}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
