# fast-nvim-release

Neovim from PyPI, in seconds, without root. Works on Linux (x86_64, arm64,
including RHEL 8-era systems), macOS (Intel, Apple silicon) and Windows
(x86_64, arm64). It is meant for CI, machines without root, and fresh servers,
containers and HPC clusters: anywhere pip already works but a package manager
does not.

```console
$ uvx fast-nvim-release setup --prefix ~/.local
installed Neovim v0.12.6 into /home/you/.local
  /home/you/.local/bin/nvim
```

`setup` itself takes about 0.2 seconds; the rest is pip fetching one ~11 MB
wheel from PyPI.

## Quickstart

Each package version carries one Neovim line. Pick the line with pip:

```console
$ pip install fast-nvim-release              # the newest line (0.12)
$ pip install "fast-nvim-release==0.11.*"    # the 0.11 line
$ fast-nvim-release setup --prefix ~/.local
installed Neovim v0.11.7 into /home/you/.local
  /home/you/.local/bin/nvim
```

`pipx install` and `uv tool install` work the same way. With uvx, in one
command: `uvx --from "fast-nvim-release==0.11.*" fast-nvim-release setup --prefix ~/.local`.

Always pin a line (`==0.11.*`), not an exact version: the line pin follows that
line's newest release, which is what you want.

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
      - run: pip install "fast-nvim-release==${{ matrix.nvim }}.*"
      - run: fast-nvim-release run --headless -c "PlenaryBustedDirectory tests"
```

Nothing here is specific to GitHub Actions: the same commands work in GitLab
CI, Jenkins or a Docker build, wherever there is a Python.

**No root or sudo.** Install for your user (or with pipx/uv, or in a venv, if
your system pip refuses `--user`) and put Neovim in `~/.local/bin`:

```console
$ pip install --user fast-nvim-release
$ fast-nvim-release setup --prefix ~/.local
```

**Fresh servers and containers.** One line, and nothing outside your home is
touched: `uvx fast-nvim-release setup --prefix ~/.local`. Without uv:

```console
$ python3 -m venv ~/.venvs/nvim && ~/.venvs/nvim/bin/pip install -q fast-nvim-release && ~/.venvs/nvim/bin/fast-nvim-release setup --prefix ~/.local
```

**Older Linux and HPC clusters.** The same commands work on RHEL 8 and similar
systems; pip picks the [glibc 2.17 wheel](#older-linux-rhel-8-centos-7-hpc-clusters)
by itself. It needs Python 3.9 or newer: on RHEL 8 the default `python3` is
3.6, so use `python3.9` (the `python39` package) or a module your site provides.

**Restricted networks.** Only PyPI, or an internal PyPI mirror, is needed,
never GitHub. For machines with no network at all, see
[Installing for other machines](#installing-for-other-machines).

## Versions

A version is `<Neovim line>.<release>`: `0.12.3` is the third release of the
0.12 line. A line's release number goes up whenever its wheels change: a new
Neovim patch, or a fix in this tool. Each line always carries the newest patch
of its Neovim line, so `pip install "fast-nvim-release==0.12.*"` gets the newest
0.12. `fast-nvim-release --version` and the
[GitHub releases](https://github.com/nickeisenberg/fast-nvim-release/releases)
say exactly which Neovim each version carries.

| Line | Neovim | Linux x86_64 | Linux arm64 | macOS | Windows x64 | Windows arm64 |
| --- | --- | --- | --- | --- | --- | --- |
| `0.10` | 0.10.4 | ✓ | — * | ✓ | ✓ | — (no official build) |
| `0.11` | 0.11.7 | ✓ | ✓ | ✓ | ✓ | ✓ |
| `0.12` | 0.12.6 | ✓ | ✓ | ✓ | ✓ | ✓ |

\* the official 0.10 arm64 build needs glibc 2.38, while every other Linux build
needs only 2.34.

Lines are never retired: older lines keep getting releases when this tool
changes, so every line from 0.10 on stays installable with the latest fixes.

pip picks the wheel from the running Python's platform: an x86_64 Python under
Rosetta 2 (or an x64 Python on Windows on ARM) gets the x86_64 build, which
works but runs emulated.

### Older Linux (RHEL 8, CentOS 7, HPC clusters)

Neovim's official Linux builds need glibc 2.34. For older systems each line
also has Linux wheels built from the builds Neovim publishes in
[`neovim/neovim-releases`](https://github.com/neovim/neovim-releases), which
need only glibc 2.17. You don't choose them: pip picks them on any system too
old for the official builds, and the official builds everywhere else. They
work with the pip that RHEL 8's Python 3.9 ships.

| Line | Older-Linux x86_64 | Older-Linux arm64 |
| --- | --- | --- |
| `0.10` | 0.10.4 | — |
| `0.11` | 0.11.7 | — |
| `0.12` | 0.12.5 | 0.12.5 |

`neovim-releases` usually trails the main releases by a patch or so, which is
why 0.12 is 0.12.5 there. Neovim calls these builds unsupported; they are built
by Neovim's CI from the same sources.

Systems with no Neovim build at all (Alpine/musl, macOS < 11, armv7, and arm64
with glibc < 2.34 for 0.10/0.11) get no wheel ("no matching distribution").

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim-release setup --prefix DIR [--force]` | Install the bundled Neovim into `DIR` |
| `fast-nvim-release path [--prefix DIR]` | Print the `nvim` this tool installed in `DIR` |
| `fast-nvim-release run [--] [ARGS]` | Run the bundled nvim without installing it |
| `fast-nvim-release --version` | This tool's version and the Neovim it carries |

### Install location

`setup` writes only where `--prefix` points; it has no default. A prefix gets an
ordinary Neovim layout:

```console
<prefix>/bin/nvim                     # the executable, which belongs on PATH
<prefix>/lib/nvim/...                 # libraries it needs
<prefix>/share/nvim/runtime/...       # runtime files it needs
<prefix>/share/fast-nvim-release/     # records what was installed here
```

A prefix holds one Neovim: running `setup` from another line replaces it, so use
separate prefixes to keep several. Files the tool did not install are never
overwritten unless you pass `--force`; other files in `<prefix>/bin` are left
alone. Nothing outside the prefix is written. If `<prefix>/bin` is not on
`PATH`, `setup` says so.

`path` exits 1 when the prefix has no install, which makes it the check to use
in scripts. Without `--prefix` it looks in `~/.local` (on Windows,
`%LOCALAPPDATA%\fast-nvim-release`).

`run` hands everything after it to Neovim verbatim:
`fast-nvim-release run -- -u NONE --headless "+q"`.

### Stability

The version number names the Neovim line, so it cannot announce breaking
changes in this tool. Instead, the command line is stable: the commands and
flags above, the prefix layout, and the exit codes (0 success, 1 error, 2 bad
usage) keep working. New commands or flags may be added; existing ones are not
removed or changed in meaning. Message wording and the Python modules inside
the package are not part of this promise.

## Installing for other machines

Each wheel carries one platform's build. To prepare all of them, for example
for machines without internet access, let pip download the other platforms'
wheels of a line:

```console
for p in manylinux_2_34_x86_64 manylinux_2_34_aarch64 macosx_11_0_x86_64 \
         macosx_11_0_arm64 win_amd64 win_arm64 manylinux2014_x86_64 manylinux2014_aarch64; do
  pip download "fast-nvim-release==0.12.*" --no-deps --only-binary=:all: --platform $p -d wheels/
done
```

On each machine, `pip install --no-index --find-links wheels/ fast-nvim-release`
picks the matching wheel. pip checks each file against the hash PyPI recorded.
Without Python on the target, unzip the wheel: `fast_nvim_release/_nvim/` inside
it is a complete Neovim tree.

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
| fast-nvim-release | no | PyPI or a PyPI mirror | yes, one line per install | CI, no root, fresh servers, restricted networks |

Use something else when you want nightly or a release older than 0.10, when
your platform has no wheel, or when you want updates to arrive with the rest of
your system.

## Contributing

Building the wheels, releasing and bumping Neovim versions are described in
[`CONTRIBUTING.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/CONTRIBUTING.md).

## License

fast-nvim-release is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see
[`THIRD_PARTY_NOTICES.md`](https://github.com/nickeisenberg/fast-nvim-release/blob/master/THIRD_PARTY_NOTICES.md)
and the `fast_nvim_release/_nvim/LICENSE.nvim-<line>.txt` file inside the wheel.
