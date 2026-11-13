# fast-nvim

Ship Neovim in a pip wheel.

```console
$ pip install fast-nvim
$ fast-nvim setup
fast-nvim 0.1.0 (Neovim v0.12.6)
   target  linux-x86_64 (linux, x86_64)
   prefix  /home/you/.local
   source  pip wheel (/home/you/.venv/lib/python3.12/site-packages/fast_nvim/_nvim/linux-x86_64)

installed 2124 files (34.4 MiB)
  /home/you/.local/bin
  /home/you/.local/lib/nvim
  /home/you/.local/share/nvim/runtime

verified  NVIM v0.12.6
$ nvim --version | head -1
NVIM v0.12.6
```

`pip install` already downloads the binary; `fast-nvim setup` puts it on your
`PATH` (by default at `~/.local/bin/nvim`, next to the runtime it needs).

Works on Linux (x86_64, arm64), macOS (Intel, Apple silicon) and Windows
(x86_64, arm64).

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim setup` | Install the bundled nvim (binary + runtime) into `~/.local` |
| `fast-nvim setup linux` / `mac` / `windows` | Same, for an explicit platform |
| `fast-nvim setup windows --prefix ./nvim-win` | Install another platform's build into a folder |
| `fast-nvim setup --list` | Show every platform and whether it is bundled, cached or needs a download |
| `fast-nvim setup --dry-run` | Show what would be written |
| `fast-nvim setup --link` | Symlink into the package instead of copying |
| `fast-nvim path [--json] [--bundle]` | Print the nvim path (or the in-package bundle dir) |
| `fast-nvim version [--json]` | fast-nvim version, Neovim release, what is bundled |
| `fast-nvim run -- [args]` | Run the bundled nvim without installing anything |

Platforms can be spelled loosely: `linux`, `mac`, `macos`, `osx`, `darwin`,
`win`, `windows`, `win64`, `linux-arm64`, `windows/amd64`, `macos-x86_64`.
The machine you are on is the default.

Everything after `fast-nvim run` goes to Neovim untouched:
`fast-nvim run -- -u NONE --headless "+q"`.

## Why one wheel per platform

A wheel can only contain one platform's executable, so each release publishes
**six wheels with the same version and different platform tags**:

```
fast_nvim-0.1.0-py3-none-manylinux_2_34_x86_64.whl   12.4 MB
fast_nvim-0.1.0-py3-none-manylinux_2_34_aarch64.whl  12.4 MB
fast_nvim-0.1.0-py3-none-macosx_11_0_x86_64.whl     10.9 MB
fast_nvim-0.1.0-py3-none-macosx_11_0_arm64.whl       10.6 MB
fast_nvim-0.1.0-py3-none-win_amd64.whl               12.6 MB
fast_nvim-0.1.0-py3-none-win_arm64.whl               13.2 MB
```

pip reads the tag and installs the one that matches the machine, so
`pip install fast-nvim` "just works" everywhere while every user downloads
~12 MB, not ~75 MB. This is the same scheme `ruff` and `uv` use.

PyPI's limits are not a problem at this size: **100 MiB per file** and
**10 GiB per project** by default (raises are granted for projects such as
`torch`, whose largest wheel is >500 MB).

If you need a wheel that works on several platforms at once, vendor more than
one build before building (`python scripts/fetch_nvim.py --platform linux
--platform windows`); `fast-nvim setup windows` then works offline too. Note
that the wheel tag will still only match one platform, so only do that for
private/offline distributions.

## The runtime has to come along

A bare `nvim` binary is not usable: Neovim resolves `$VIMRUNTIME` relative to
its own executable (`../share/nvim/runtime`). Without it you get
`E484: Can't open file /usr/local/share/nvim/syntax/syntax.vim`.

So the wheel ships the whole runnable tree, which is what `fast-nvim setup`
installs:

```
fast_nvim/_nvim/<platform>/bin/nvim          the binary (plus DLLs on Windows)
fast_nvim/_nvim/<platform>/lib/nvim/parser/  tree-sitter parsers
fast_nvim/_nvim/<platform>/share/nvim/runtime/  ~27 MiB of Lua/Vim runtime
```

Only `bin/`, `lib/nvim/` and `share/nvim/runtime/` are vendored; desktop
files, icons and the man page are dropped.

Two details worth knowing:

* Wheels are zip files and pip does not always restore the executable bit, so
  fast-nvim `chmod +x`es the binary before using it (and again on `setup`).
* The Linux builds from Neovim require **glibc >= 2.34**, hence the
  `manylinux_2_34` tag. musl systems (Alpine) are not supported by Neovim's
  official builds.

## Publishing to PyPI

The repo is set up for [trusted publishing](https://docs.pypi.org/trusted-publishers/)
—no API tokens in CI.

1. Bump `__version__` in `src/fast_nvim/__init__.py`.
2. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
3. `.github/workflows/wheels.yml` builds all six wheels, checks that each one
   contains exactly one Neovim build, and publishes to PyPI.

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
$ pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple fast-nvim
```

Prefer `python scripts/build_wheels.py --all` for local builds: it vendors each
platform into a throwaway copy of the tree and sets `FAST_NVIM_PLAT_TAG`, so
cross-built wheels are tagged correctly. Never upload a wheel whose tag does
not match its payload—`setup.py` refuses to build one when it can tell, and
`scripts/check_wheels.py` checks the finished artifacts (tag vs. payload,
runtime present, `Root-Is-Purelib: false`, size under PyPI's limit).

Only wheels are published—no source distribution. Building from an sdist on a
platform with no vendored binary would produce a useless package, so `setup.py`
aborts that build instead; on an unsupported platform pip simply reports that
no matching distribution exists.

Before your first upload, register the project's publisher on PyPI
(Your projects → Publishing) for owner/repo and the `pypi` environment used by
the workflow. The name `fast-nvim` is currently unclaimed on PyPI.

## Updating Neovim

Neovim's version and the sha256 of every release asset are pinned in
`src/fast_nvim/platforms.py` (`NVIM_VERSION`, `TARGETS`). To move to a new
release:

```console
$ # bump NVIM_VERSION and the sha256/size values, then:
$ python scripts/fetch_nvim.py --all --force
$ python scripts/build_wheels.py --all
```

`scripts/fetch_nvim.py` verifies every download against its pinned sha256 and,
for the host platform, runs `nvim --version` to confirm the binary works.

## Development

```console
$ python -m pytest                 # 54 tests, no network needed
$ python scripts/fetch_nvim.py     # vendor this platform into the source tree
$ python -m build --wheel          # single wheel for this platform
```

Layout:

```
src/fast_nvim/cli.py        setup / path / version / run
src/fast_nvim/bundle.py     vendored bundles, cache, extraction, installation
src/fast_nvim/platforms.py  Neovim release pin + platform matrix (single source of truth)
setup.py                   platform wheel tags, refuses empty wheels
scripts/fetch_nvim.py      download/extract release archives into the package
scripts/build_wheels.py    build all six wheels from one machine
```

## Notes

* `fast-nvim setup` never overwrites an `nvim` it did not install; use
  `--force` if you really mean it.
* Re-running `setup` upgrades an existing fast-nvim install in place.
* `--link` keeps disk usage at zero extra bytes but breaks if the virtualenv
  is removed; the default is to copy (the runtime is ~35 MB).
* Uninstalling the package does not remove a copy made by `fast-nvim setup`.
  Delete `~/.local/bin/nvim`, `~/.local/lib/nvim`, `~/.local/share/nvim/runtime`
  and `~/.local/share/fast-nvim` to undo it.

## License

fast-nvim is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see `THIRD_PARTY_NOTICES.md` and the
`fast_nvim/_nvim/LICENSE.nvim.txt` file inside the wheel.
