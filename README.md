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

(Neovim's official Linux builds need glibc ≥ 2.34, hence `manylinux_2_34`;
musl systems such as Alpine are not covered by them.)

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

## Development

```console
$ python -m pytest                 # 132 tests, no network needed
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
scripts/smoke_test.py           install a wheel in a clean venv and use it
```

## License

fast-nvim-latest is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see `THIRD_PARTY_NOTICES.md` and the
`fast_nvim_latest/_nvim/LICENSE.nvim.txt` file inside the wheel.
