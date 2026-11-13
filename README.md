# fast-nvim-release

[![PyPI](https://img.shields.io/pypi/v/fast-nvim-release)](https://pypi.org/project/fast-nvim-release/)

Neovim 0.10, 0.11 and 0.12 from PyPI, in seconds, without root. Works on Linux
(x86_64, arm64, including RHEL 8-era systems), macOS (Intel, Apple silicon) and
Windows (x86_64, arm64). It is meant for CI, machines without root, and fresh
servers, containers and HPC clusters: anywhere pip already works but a package
manager does not.

```console
$ time uvx fast-nvim-release setup 0.12 --prefix ~/.local
installed Neovim v0.12.6 into /home/you/.local
  /home/you/.local/bin/nvim

real    0m2.540s
```

That is from a cold start, downloading included. `setup` itself takes about
0.2 seconds; the rest is fetching the wheel (25–36 MB) from PyPI. With plain
pip, creating a new venv first, the whole thing takes about 5 seconds.

## Quickstart

```console
$ pip install fast-nvim-release      # or: pipx install / uv tool install fast-nvim-release
$ fast-nvim-release setup 0.12 --prefix ~/nvim
installed Neovim v0.12.6 into /home/you/nvim
  /home/you/nvim/bin/nvim
$ ~/nvim/bin/nvim --version | head -1
NVIM v0.12.6
```

Or in one command, without installing the tool itself:
`uvx fast-nvim-release setup 0.12 --prefix ~/.local`.

## Who this is for

Not the everyday desktop user who installs Neovim once (see
[Other ways to get Neovim](#other-ways-to-get-neovim)). It is for places where
pip is already there and only PyPI is reachable. Nothing is downloaded at
`setup` or `run` time: the binaries are inside the wheel.

**CI: testing a plugin against several Neovim versions.** It was built for the
CI of [iron.nvim](https://github.com/Vigemus/iron.nvim). The test command below
is only an example; use your plugin's own.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        nvim: ["0.10", "0.11", "0.12"]  # quoted, or YAML reads 0.10 as 0.1
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install fast-nvim-release
      - run: fast-nvim-release run ${{ matrix.nvim }} --headless -c "PlenaryBustedDirectory tests"
```

Nothing here is specific to GitHub Actions: the same two commands work in
GitLab CI, Jenkins or a Docker build, wherever there is a Python.

**No root or sudo.** Install for your user (or with pipx/uv, or in a venv, if
your system pip refuses `--user`) and put Neovim in `~/.local/bin`:

```console
$ pip install --user fast-nvim-release
$ fast-nvim-release setup --prefix ~/.local
```

**Fresh servers and containers.** One line, and nothing outside your home is
touched:

```console
$ uvx fast-nvim-release setup --prefix ~/.local
```

Without uv:

```console
$ python3 -m venv ~/.venvs/nvim && ~/.venvs/nvim/bin/pip install -q fast-nvim-release && ~/.venvs/nvim/bin/fast-nvim-release setup --prefix ~/.local
```

**Older Linux and HPC clusters.** The same commands work on RHEL 8 and similar
systems; pip picks the [glibc 2.17 wheel](#older-linux-rhel-8-centos-7-hpc-clusters)
by itself. It needs Python 3.9 or newer: on RHEL 8 the default `python3` is
3.6, so use `python3.9` (the `python39` package) or a module your site provides.

**Restricted networks.** Only PyPI, or an internal PyPI mirror, is needed,
never GitHub: `pip install --index-url https://pypi.example.internal/simple
fast-nvim-release`. For machines with no network at all, see
[Installing for other machines](#installing-for-other-machines).

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
Windows on ARM) gets the x86_64 build, which works but runs emulated.

### Older Linux (RHEL 8, CentOS 7, HPC clusters)

Neovim's official Linux builds need glibc 2.34. For older systems there is a
second Linux wheel built from the builds Neovim publishes in
[`neovim/neovim-releases`](https://github.com/neovim/neovim-releases), which
need only glibc 2.17. You don't choose it: `pip install fast-nvim-release`
picks it on any system too old for the official builds, and picks the official
builds everywhere else. It works with the pip that RHEL 8's Python 3.9 ships.

| Release | Older-Linux x86_64 | Older-Linux arm64 |
| --- | --- | --- |
| `0.10` | 0.10.4 | — |
| `0.11` | 0.11.7 | — |
| `0.12` | 0.12.5 | 0.12.5 |

`neovim-releases` usually trails the main releases by a patch or so, which is
why 0.12 is 0.12.5 there. Neovim calls these builds unsupported; they are built
by Neovim's CI from the same sources.

Systems with no Neovim build at all (Alpine/musl, macOS < 11, armv7, and
arm64 with glibc < 2.34 for 0.10/0.11) get no wheel ("no matching
distribution").

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
         macosx_11_0_arm64 win_amd64 win_arm64 manylinux2014_x86_64 manylinux2014_aarch64; do
  pip download fast-nvim-release --no-deps --only-binary=:all: --platform $p -d wheels/
done
```

On each machine, `pip install --no-index --find-links wheels/ fast-nvim-release`
picks the matching wheel. pip checks each file against the hash PyPI recorded.
Without Python on the target, unzip the wheel: `fast_nvim_release/_nvim/<release>/<platform>/`
inside it is a complete Neovim tree.

## Other ways to get Neovim

There are good, widely used ways to install Neovim, and on your own desktop a
package manager or bob is usually the better choice: they keep Neovim up to
date, and bob also handles nightly builds. fast-nvim-release is aimed at CI,
non-root and throwaway environments, where pip is already present and only
PyPI is reachable.

| Option | Root needed | Downloads from | Several versions side by side | Good fit |
| --- | --- | --- | --- | --- |
| OS package manager (apt, dnf, Homebrew, winget, scoop, ...) | for apt/dnf, yes | your distribution's or the tool's repositories | usually no | your own machine |
| [bob](https://github.com/MordechaiHadad/bob) | no | GitHub | yes, including nightly | your own machine, switching versions |
| Official tarball / AppImage | no | GitHub releases | by hand | any machine with GitHub access |
| mise / asdf | no | usually GitHub releases | yes | if you already manage tools with them |
| Neovim setup actions for GitHub Actions | no | usually GitHub releases | per job | GitHub Actions only |
| fast-nvim-release | no | PyPI or a PyPI mirror | yes (0.10, 0.11, 0.12) | CI, no root, fresh servers, restricted networks |

Use something else when you want nightly or a release older than 0.10, when
your platform has no wheel (see [Supported releases](#supported-releases)), or
when you want updates to arrive with the rest of your system.

## Contributing

Building the wheels and bumping a Neovim release are described in
[`CONTRIBUTING.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/CONTRIBUTING.md).

## License

fast-nvim-release is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see [`THIRD_PARTY_NOTICES.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/THIRD_PARTY_NOTICES.md) and the
`fast_nvim_release/_nvim/LICENSE.nvim-<release>.txt` files inside the wheel.
