# fast-nvim-release

Get the last three minor releases of nvim in under 5 seconds. Works on Linux
(x86_64, arm64), macOS (Intel, Apple silicon) and Windows (x86_64, arm64).

```console
$ time bash -c 'python -m venv ~/get-nvim && \
source ~/get-nvim/bin/activate && \
pip install -q fast-nvim-release && \
fast-nvim-release setup 0.12 --prefix ~/nvim && \
~/nvim/bin/nvim --version | head -1' >/dev/null 2>/dev/null

real    0m3.944s
user    0m3.356s
sys     0m0.443s
```

## Quickstart

```console
$ pip install fast-nvim-release
$ fast-nvim-release setup 0.12 --prefix ~/nvim
installed Neovim v0.12.6 into /home/you/nvim
  /home/you/nvim/bin/nvim
$ ~/nvim/bin/nvim --version | head -1
NVIM v0.12.6
```

## Supported releases

The wheel for your platform carries every release line, each pinned to its
newest patch, so switching needs no network:

| Release | Neovim | Linux x86_64 | Linux arm64 | macOS | Windows x64 | Windows arm64 |
| --- | --- | --- | --- | --- | --- | --- |
| `0.10` | 0.10.4 | ✓ | — * | ✓ | ✓ | — (no official build) |
| `0.11` | 0.11.7 | ✓ | ✓ | ✓ | ✓ | ✓ |
| `0.12` | 0.12.6 | ✓ | ✓ | ✓ | ✓ | ✓ |

\* the official 0.10 arm64 build needs glibc 2.38, while every other Linux build
needs only 2.34; shipping it would drop Ubuntu 22.04, Debian 12 and RHEL 9 from
the arm64 wheel. Use 0.11 or 0.12 there.

`0.12` is the default. A release can be written `0.12`, `v0.12`, `12` or
`0.12.6`.

pip picks the wheel from the running Python's platform, and you get that
platform's builds: an x86_64 Python under Rosetta 2 (or an x64 Python on
Windows on ARM) gets the x86_64 build, which works but runs emulated. Systems
without an official Neovim build (Alpine/musl, glibc < 2.34, macOS < 11, armv7)
get no wheel ("no matching distribution").

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim-release setup [RELEASE] --prefix DIR [--force]` | Install a release (default 0.12) into `DIR` |
| `fast-nvim-release path [--prefix DIR]` | Print the `nvim` this tool installed in `DIR` |
| `fast-nvim-release run [RELEASE] [--] [ARGS]` | Run a bundled nvim without installing it |
| `fast-nvim-release --version` | This tool's version and the Neovim builds it carries |

### Install location

`setup` writes only where `--prefix` points; it has no default. A prefix gets an
ordinary Neovim layout:

```console
<prefix>/bin/nvim                     # the executable, which belongs on PATH
<prefix>/lib/nvim/...                 # libraries it needs
<prefix>/share/nvim/runtime/...       # runtime files it needs
<prefix>/share/fast-nvim-release/     # records what was installed here
```

A prefix holds one Neovim: installing another release into it replaces the
previous one, so use separate prefixes to keep several. Files the tool did not
install are never overwritten unless you pass `--force`; other files in
`<prefix>/bin` are left alone. Nothing outside the prefix is written. If
`<prefix>/bin` is not on `PATH`, `setup` says so.

`path` exits 1 when the prefix has no install, which makes it the check to use
in scripts. Without `--prefix` it looks in `~/.local` (on Windows,
`%LOCALAPPDATA%\fast-nvim-release`).

### Running without installing

`run` takes an optional release and hands everything else to Neovim verbatim:
`fast-nvim-release run -- -u NONE --headless "+q"`, or
`fast-nvim-release run 0.10 file.txt`. Only a version is read as the release, so
`run notes.md` opens `notes.md`; put `--` first to open a file named like a
version.

## Installing for other machines

Each wheel carries one platform's builds. To prepare all of them, for example
for machines without internet access, let pip download the other platforms'
wheels:

```console
for p in manylinux_2_34_x86_64 manylinux_2_34_aarch64 macosx_11_0_x86_64 \
         macosx_11_0_arm64 win_amd64 win_arm64; do
  pip download fast-nvim-release --no-deps --only-binary=:all: --platform $p -d wheels/
done
```

On each machine, `pip install --no-index --find-links wheels/ fast-nvim-release`
picks the matching wheel. pip checks each file against the hash PyPI recorded.
Without Python on the target, unzip the wheel: `fast_nvim_release/_nvim/<release>/<platform>/`
inside it is a complete Neovim tree.

## Contributing

Building the wheels and bumping a Neovim release are described in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

fast-nvim-release is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see `THIRD_PARTY_NOTICES.md` and the
`fast_nvim_release/_nvim/LICENSE.nvim-<release>.txt` files inside the wheel.
