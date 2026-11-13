#!/usr/bin/env python3
"""Install a built wheel into a clean venv and use it like a user would.

    python scripts/smoke_test.py [dist/fast_nvim_release-...-<this platform>.whl]

Runs ``--version``, ``run``, ``setup`` and ``path`` for every release the
wheel carries, and checks the installed nvim reports the right version.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release.platforms import RELEASES, host_platform  # noqa: E402

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


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    here = host_platform()
    if here is None:
        raise SystemExit("unknown host platform")
    wheels = [Path(argv[0])] if argv else sorted((ROOT / "dist").glob(f"*-{here.wheel_tag}.whl"))
    if not wheels:
        raise SystemExit(f"no {here.wheel_tag} wheel in dist/")
    wheel = wheels[-1].resolve()
    releases = [key for key, release in RELEASES.items() if here.key in release.sha256]

    with tempfile.TemporaryDirectory(prefix="fast-nvim-release-smoke-") as tmp:
        work = Path(tmp)
        venv.create(work / "venv", with_pip=True)
        scripts = work / "venv" / ("Scripts" if os.name == "nt" else "bin")
        suffix = ".exe" if os.name == "nt" else ""
        python = scripts / f"python{suffix}"
        cli = scripts / f"fast-nvim-release{suffix}"
        run(python, "-m", "pip", "install", "--no-deps", "-q", wheel)

        out = run(cli, "--version")
        for key in releases:
            assert RELEASES[key].version in out, out
        run(cli, "setup", ok=False)  # --prefix is required

        prefix = work / "prefix"
        for key in releases:
            version = RELEASES[key].version
            assert version in run(cli, "run", key, "--", "--version").splitlines()[0]
            run(cli, "setup", key, "--prefix", prefix)
            assert version in run(prefix / "bin" / here.exe, "--version").splitlines()[0]
            print(f"  {key}: ok ({version})")
        assert str(prefix) in run(cli, "path", "--prefix", prefix)

    print(f"PASS {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
