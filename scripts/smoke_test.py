#!/usr/bin/env python3
"""Install built wheels into a clean venv and use them like a user would.

    python scripts/smoke_test.py WHEEL [WHEEL...]

For each wheel (it must run on this machine): ``--version``, ``run``,
``setup`` and ``path``, and the installed nvim must report the Neovim version
the wheel's ``build.json`` names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

#: a user's $VIMRUNTIME must not decide whether the smoke test passes
ENV = {k: v for k, v in os.environ.items() if k not in ("VIMRUNTIME", "VIM")}


def run(*cmd, ok: bool = True) -> str:
    print("$", *cmd, flush=True)
    result = subprocess.run(
        [str(part) for part in cmd], capture_output=True, text=True, timeout=300, env=ENV
    )
    if (result.returncode == 0) != ok:
        raise SystemExit(f"FAILED (exit {result.returncode}):\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def smoke(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        info = json.loads(zf.read("fast_nvim_release/_nvim/build.json"))
    neovim, version = info["neovim"], info["version"]
    exe = "nvim.exe" if os.name == "nt" else "nvim"
    with tempfile.TemporaryDirectory(prefix="fast-nvim-release-smoke-") as tmp:
        work = Path(tmp)
        venv.create(work / "venv", with_pip=True)
        scripts = work / "venv" / ("Scripts" if os.name == "nt" else "bin")
        suffix = ".exe" if os.name == "nt" else ""
        python, cli = scripts / f"python{suffix}", scripts / f"fast-nvim-release{suffix}"
        run(python, "-m", "pip", "install", "--no-deps", "-q", wheel)

        out = run(cli, "--version")
        assert version in out and neovim in out, out
        assert neovim in run(cli, "run", "--", "--version").splitlines()[0]
        run(cli, "setup", ok=False)  # --prefix is required
        prefix = work / "prefix"
        run(cli, "setup", "--prefix", prefix)
        assert neovim in run(prefix / "bin" / exe, "--version").splitlines()[0]
        assert str(prefix) in run(cli, "path", "--prefix", prefix)
    print(f"PASS {wheel.name} (Neovim {neovim})")


def main(argv=None) -> int:
    wheels = [Path(name).resolve() for name in (sys.argv[1:] if argv is None else argv)]
    if not wheels:
        raise SystemExit("usage: smoke_test.py WHEEL [WHEEL...]")
    for wheel in wheels:
        smoke(wheel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
