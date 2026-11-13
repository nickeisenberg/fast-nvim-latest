# fast-nvim-latest

Ship Neovim in a pip wheel.

```console
$ pip install fast-nvim-latest
$ fast-nvim-latest setup
fast-nvim-latest 0.1.0 (Neovim v0.12.6)
   target  linux-x86_64 (linux, x86_64)
   prefix  /home/you/.local
   source  pip wheel (/home/you/.venv/lib/python3.12/site-packages/fast_nvim_latest/_nvim/linux-x86_64)

installed 2124 files (34.4 MiB)
  /home/you/.local/bin
  /home/you/.local/lib/nvim
  /home/you/.local/share/nvim/runtime

verified  NVIM v0.12.6
$ nvim --version | head -1
NVIM v0.12.6
```

`pip install` already downloads the binary; `fast-nvim-latest setup` puts it on
your `PATH` (by default at `~/.local/bin/nvim`, next to the runtime it needs).

Works on Linux (x86_64, arm64), macOS (Intel, Apple silicon) and Windows
(x86_64, arm64).

## What "latest" means

Every `fast-nvim-latest` release vendors the newest stable Neovim release
available at that moment; that one release is pinned in
`src/fast_nvim_latest/platforms.py` (`NVIM_VERSION`) and its sha256 is
verified. So "latest" describes *which Neovim you get*: the wheel always
installs the release it was built for, never a moving target.

Getting a newer Neovim is a package upgrade, not a runtime download:

```console
$ pip install -U fast-nvim-latest
$ fast-nvim-latest setup        # replaces the previous install in place
```

## Which build you get

pip picks the wheel from the **running interpreter's** platform tag, and
fast-nvim-latest installs the build that matches that wheel, so the two can
never disagree:

| Your machine | Python | Wheel / build you get |
| --- | --- | --- |
| Linux x86_64 (glibc ≥ 2.34) | any | `manylinux_2_34_x86_64` |
| Linux arm64 | any | `manylinux_2_34_aarch64` |
| Apple silicon | native arm64 | `macosx_11_0_arm64` |
| Apple silicon | x86_64 (Rosetta 2, Intel Homebrew/conda) | `macosx_11_0_x86_64` — works, slower, **`setup` warns** |
| Intel Mac | any | `macosx_11_0_x86_64` |
| Windows x64 | any | `win_amd64` |
| Windows on ARM | x64 Python | `win_amd64` — runs emulated, **`setup` warns** |
| Windows on ARM | arm64 Python | `win_arm64` |
| Alpine/musl, glibc < 2.34, macOS < 11, armv7 | any | no wheel (pip: "no matching distribution") |

If the interpreter is emulated (Rosetta 2, or an x64 Python on ARM64 Windows)
`setup` says so — the build is the *right* one for that interpreter, just not
the native one. `fast-nvim-latest version` shows it too
(`python  x86_64 (emulated on arm64 hardware)`). To get the native build,
install the package from a native interpreter.

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim-latest setup` | Install the bundled nvim (binary + runtime) into `~/.local` |
| `fast-nvim-latest setup linux` / `mac` / `windows` | Same, for an explicit platform (another platform's build needs `--prefix`) |
| `fast-nvim-latest setup windows --prefix ./nvim-win` | Install another platform's build into a folder |
| `fast-nvim-latest setup --list` | Show every platform and whether it is bundled, cached or needs a download |
| `fast-nvim-latest setup --dry-run` | Show what would be written (fetches nothing) |
| `fast-nvim-latest setup --link` | Symlink into the package instead of copying |
| `fast-nvim-latest path [--json] [--bundle]` | Print the nvim path (or the in-package bundle dir) |
| `fast-nvim-latest version [--json]` | fast-nvim-latest version, Neovim release, what is bundled |
| `fast-nvim-latest run -- [args]` | Run the bundled nvim without installing anything |

Platforms can be spelled loosely: `linux`, `mac`, `macos`, `osx`, `darwin`,
`win`, `windows`, `win64`, `linux-arm64`, `windows/amd64`, `macos-x86_64`.
The machine you are on is the default.

Everything after `fast-nvim-latest run` goes to Neovim untouched:
`fast-nvim-latest run -- -u NONE --headless "+q"`.

## Why one wheel per platform

A wheel can only contain one platform's executable, so each release publishes
**six wheels with the same version and different platform tags**:

```
fast_nvim_latest-0.1.0-py3-none-manylinux_2_34_x86_64.whl   12.4 MB
fast_nvim_latest-0.1.0-py3-none-manylinux_2_34_aarch64.whl  12.4 MB
fast_nvim_latest-0.1.0-py3-none-macosx_11_0_x86_64.whl      10.9 MB
fast_nvim_latest-0.1.0-py3-none-macosx_11_0_arm64.whl       10.7 MB
fast_nvim_latest-0.1.0-py3-none-win_amd64.whl               12.7 MB
fast_nvim_latest-0.1.0-py3-none-win_arm64.whl               13.2 MB
```

pip reads the tag and installs the one that matches the machine, so
`pip install fast-nvim-latest` "just works" everywhere while every user
downloads ~12 MB, not ~75 MB. This is the same scheme `ruff` and `uv` use.

PyPI's limits are not a problem at this size: **100 MiB per file** and
**10 GiB per project** by default (raises are granted for projects such as
`torch`, whose largest wheel is >500 MB).

If you need a wheel that works on several platforms at once, vendor more than
one build before building (`python scripts/fetch_nvim.py --platform linux
--platform windows`); `fast-nvim-latest setup windows` then works offline too.
Note that the wheel tag will still only match one platform, so only do that for
private/offline distributions (`scripts/check_wheels.py` accepts the extra
bundles but still requires the tagged platform's build to be present).

## The runtime has to come along

A bare `nvim` binary is not usable: Neovim resolves `$VIMRUNTIME` relative to
its own executable (`../share/nvim/runtime`). Without it you get
`E484: Can't open file /usr/local/share/nvim/syntax/syntax.vim`.

So the wheel ships the whole runnable tree, which is what
`fast-nvim-latest setup` installs:

```
fast_nvim_latest/_nvim/<platform>/bin/nvim          the binary (plus DLLs on Windows)
fast_nvim_latest/_nvim/<platform>/lib/nvim/parser/  tree-sitter parsers
fast_nvim_latest/_nvim/<platform>/share/nvim/runtime/  ~27 MiB of Lua/Vim runtime
fast_nvim_latest/_nvim/LICENSE.nvim.txt             Neovim's license (tracked in git)
fast_nvim_latest/_nvim/BUILD_INFO.json              what was vendored, with sha256
```

Only `bin/`, `lib/nvim/` and `share/nvim/runtime/` are vendored; desktop
files, icons and the man page are dropped.

Two details worth knowing:

* Wheels are zip files and pip does not always restore the executable bit, so
  fast-nvim-latest `chmod +x`es the binary before using it (and again on `setup`).
* The Linux builds from Neovim require **glibc >= 2.34**, hence the
  `manylinux_2_34` tag. musl systems (Alpine) are not supported by Neovim's
  official builds.

## Publishing to PyPI

The repo is set up for [trusted publishing](https://docs.pypi.org/trusted-publishers/)
—no API tokens in CI.

1. Bump `__version__` in `src/fast_nvim_latest/__init__.py`.
2. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
3. `.github/workflows/wheels.yml` builds all six wheels, validates each one
   (tag vs. binary, runtime, license, version == tag) and publishes to PyPI.

To publish by hand:

```console
$ python scripts/fetch_nvim.py --all        # vendor all six builds (or use the cache)
$ python scripts/build_wheels.py --all      # dist/*.whl, ~12 MB each
$ python -m twine check dist/*
$ python -m twine upload dist/*
```

For a dry run, publish to TestPyPI and install from there:

```console
$ python -m twine upload --repository testpypi dist/*
$ pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple fast-nvim-latest
```

Prefer `python scripts/build_wheels.py --all` for local builds: it vendors each
platform into a throwaway copy of the tree and sets
`FAST_NVIM_LATEST_PLAT_TAG`, so cross-built wheels are tagged correctly. Never
upload a wheel whose tag does not match its payload—`setup.py` refuses to build
one when it can tell, and `scripts/check_wheels.py` checks the finished
artifacts (tag vs. payload, the architecture and glibc/macOS floor *read out of
the binary*, the runtime, `Root-Is-Purelib: false`, Neovim's license, one
version across all wheels and size under PyPI's limit).

Only wheels are published—no source distribution. Building from an sdist on a
platform with no vendored binary would produce a useless package, so `setup.py`
aborts that build instead; on an unsupported platform pip simply reports that
no matching distribution exists.

Before your first upload, register the project's publisher on PyPI
(Your projects → Publishing) for owner/repo and the `pypi` environment used by
the workflow. The names `fast-nvim-latest` and `fast-nvim` are both currently
unclaimed on PyPI (checked 2026-11-13).

## Updating Neovim

Neovim's version and the sha256 of every release asset are pinned in
`src/fast_nvim_latest/platforms.py` (`NVIM_VERSION`, `TARGETS`). To move to a
new release:

```console
$ # bump NVIM_VERSION and the sha256/size values, then:
$ python scripts/fetch_nvim.py --all
$ python scripts/build_wheels.py --all
```

`scripts/fetch_nvim.py` verifies every download against its pinned sha256,
re-vendors a build whose `BUILD_INFO.json` no longer matches the pin (so a
version bump cannot silently keep the old binary), and for the host platform
runs `nvim --version` to confirm the binary works.

## Environment

| Variable | Effect |
| --- | --- |
| `FAST_NVIM_LATEST_PREFIX` | Default install prefix, same as `--prefix` |
| `FAST_NVIM_LATEST_CACHE_DIR` | Where downloaded archives/trees are kept (`~/.cache/fast-nvim-latest`) |
| `XDG_CACHE_HOME` | Used for the cache location when set |
| `NO_COLOR` / `FORCE_COLOR` | Turn the little colour there is off / on |

## Development

```console
$ python -m pytest                 # 114 tests, no network needed
$ python -m ruff check .           # lint (CI runs this)
$ python -m ruff format --check .
$ python scripts/fetch_nvim.py     # vendor this platform into the source tree
$ python -m build --wheel          # single wheel for this platform
```

Layout:

```
src/fast_nvim_latest/cli.py     setup / path / version / run
src/fast_nvim_latest/bundle.py  vendored bundles, cache, extraction, installation
src/fast_nvim_latest/platforms.py  Neovim pin + platform matrix (single source of truth)
setup.py                        platform wheel tags, refuses empty/mislabelled wheels
scripts/fetch_nvim.py           download/extract release archives into the package
scripts/build_wheels.py         build all six wheels from one machine
scripts/check_wheels.py         validate wheels (tag vs. binary, runtime, license)
scripts/smoke_test.py      install a wheel in a clean venv and use it
```

## Notes

* `fast-nvim-latest setup` never overwrites an `nvim`, `lib/nvim` or
  `share/nvim/runtime` it did not install; use `--force` if you really mean it.
* Re-running `setup` upgrades an existing fast-nvim-latest install in place and
  removes the files the previous version owned, so you never end up with a mix
  of two Neovim runtimes.
* The post-install check runs the installed binary and compares the
  `$VIMRUNTIME` it actually resolved with the prefix (`--no-verify` skips it).
  `nvim --version` alone would pass even with the runtime missing.
* `--link` keeps disk usage at zero extra bytes but breaks if the virtualenv
  is removed; the default is to copy (the runtime is ~35 MB).
* Uninstalling the package does not remove a copy made by
  `fast-nvim-latest setup`. Delete `~/.local/bin/nvim`, `~/.local/lib/nvim`,
  `~/.local/share/nvim/runtime` and `~/.local/share/fast-nvim-latest` to undo
  it (on Windows the prefix is `%LOCALAPPDATA%\fast-nvim-latest`).

## License

fast-nvim-latest is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see `THIRD_PARTY_NOTICES.md` and the
`fast_nvim_latest/_nvim/LICENSE.nvim.txt` file inside the wheel.
