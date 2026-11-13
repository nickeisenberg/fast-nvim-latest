# fast-nvim-release

Get the last three minor releases of nvim in under 10 seconds. Works on Linux
(x86_64, arm64), macOS (Intel, Apple silicon) and Windows (x86_64, arm64).

```console
$ time bash -c 'python -m venv ~/get-nvim && \
source ~/get-nvim/bin/activate && \
pip install -q fast-nvim-release && \
mkdir -p ~/nvim && \
fast-nvim-release setup --prefix ~/nvim --quiet && \
~/nvim/bin/nvim --version | head -1' >/dev/null 2>/dev/null

real    0m3.944s
user    0m3.356s
sys     0m0.443s
```

## Supported releases

Every release line is pinned to its newest patch release:

| Release | Neovim | Linux | macOS | Windows x64 | Windows arm64 |
| --- | --- | --- | --- | --- | --- |
| `0.10` | 0.10.4 | ✓ * | ✓ | ✓ | — (Neovim had no arm64 build) |
| `0.11` | 0.11.7 | ✓ | ✓ | ✓ | ✓ |
| `0.12` | 0.12.6 | ✓ | ✓ | ✓ | ✓ |

\* on Linux arm64 the 0.10 build needs glibc 2.38; on older systems (Ubuntu
22.04, Debian 12, RHEL 9) `setup 0.10` refuses instead of installing a binary
that cannot start. Use 0.11 or 0.12 there.

`0.12` is the default. Pick any of them when installing:

```console
$ fast-nvim-release setup 0.10 --prefix ~/.local       # ~/.local/bin/nvim from 0.10.4
$ fast-nvim-release setup 0.10 --prefix ~/nvim-0-10
$ fast-nvim-release setup 0.12 windows --prefix ./dist-win
```

A prefix holds exactly one build, so installing 0.10 into the same prefix as
0.12 replaces the install in place; see [Install location](#install-location)
for the layout and how to keep several.

Release selectors are loose, like platforms: `0.12`, `12`, `v0.12.6`, `latest`
all mean the same thing, and the selector and platform can be given in either
order.

### What is in the wheel, and what is downloaded

`pip install` vendors Neovim **0.10.4, 0.11.7 and 0.12.6** for your platform, so
switching releases needs no network. One exception: **0.10 on Linux arm64** is
fetched from GitHub, checksum-pinned, the first time you ask for it.

The official 0.10 arm64 build is linked against glibc 2.38, while 0.11 and 0.12
need only 2.34. Vendoring it would force the whole arm64 wheel up to
`manylinux_2_38` and silently drop Ubuntu 22.04, Debian 12 and RHEL 9 — so that
one build is served on demand instead. Every other build ships in the wheel.

Because of that floor, `setup 0.10` checks this machine's glibc on Linux arm64
and refuses before downloading anything when it is older than 2.38. Say
`fast-nvim-release setup 0.11 --prefix ~/.local` (or `0.12`) on those systems.

## Which build you get

pip picks the wheel from the **running interpreter's** platform tag, and
fast-nvim-release installs the builds that match that wheel, so the two can
never disagree:

| Your machine | Python | Wheel / builds you get |
| --- | --- | --- |
| Linux x86_64 (glibc ≥ 2.34) | any | `manylinux_2_34_x86_64` |
| Linux arm64 | any | `manylinux_2_34_aarch64` (0.10 fetched on demand, needs glibc 2.38) |
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
the native one. `fast-nvim-release version` shows it too
(`python  x86_64 (emulated on arm64 hardware)`). To get the native build,
install the package from a native interpreter.

## Commands

| Command | What it does |
| --- | --- |
| `fast-nvim-release setup [RELEASE] --prefix DIR` | Install a release (default: 0.12) into `DIR` |
| `fast-nvim-release setup 0.12 windows --prefix ./nvim-win` | Install another platform's build |
| `fast-nvim-release setup --list` | Show every release and what is bundled or downloaded |
| `fast-nvim-release setup --prefix DIR --dry-run` | Show what would be written (fetches nothing) |
| `fast-nvim-release setup --prefix DIR --link` | Symlink into the package instead of copying |
| `fast-nvim-release path [RELEASE] [--prefix DIR] [--json] [--bundle]` | Print the nvim path (or the in-package bundle dir) |
| `fast-nvim-release version [RELEASE] [--prefix DIR] [--json]` | Versions, supported releases, what is bundled |
| `fast-nvim-release run [RELEASE] -- [args]` | Run a bundled nvim without installing anything |

### Install location

`setup` writes only where you point it. `--prefix` is required and has no
default, and there is no environment variable for it either: a forgotten
argument cannot land on an existing install. A prefix is an ordinary Neovim
layout:

```console
<prefix>/bin/nvim                     # the executable, which belongs on PATH
<prefix>/lib/nvim/...                 # libraries it needs
<prefix>/share/nvim/runtime/...       # runtime files it needs
<prefix>/share/fast-nvim-release/     # marker naming the build installed here
```

One prefix holds one build: installing a different release into the same prefix
replaces it in place, so use separate `--prefix` directories to keep several
side by side. Nothing outside that prefix and the download cache is written —
no dotfiles, no shell configuration, no registry entries — so removing an
install means deleting the prefix.

`setup --list` installs nothing and takes no prefix. `path` and `version` only
inspect, so they still fall back to `~/.local` (on Windows,
`%LOCALAPPDATA%\fast-nvim-release`) when `--prefix` is omitted. If
`<prefix>/bin` is not on `PATH`, `setup` prints the line to add.

Platforms can be spelled loosely: `linux`, `mac`, `macos`, `osx`, `darwin`,
`win`, `windows`, `win64`, `linux-arm64`, `windows/amd64`, `macos-x86_64`.
The machine you are on is the default.

`run` takes an optional release and then hands everything else to Neovim
verbatim, flags included (put `--` after the release if a Neovim argument could
be mistaken for one): `fast-nvim-release run -- -u NONE --headless "+q"`, or
`fast-nvim-release run 0.10 -- --version`.

### Scripting

`version --json` and `path --json` print one object, sorted by key, on stdout:

```console
$ fast-nvim-release version --json | jq -r .nvim_version
v0.12.6
$ fast-nvim-release path --json | jq -r .exe
/home/you/.local/bin/nvim
```

`version` reports `fast_nvim_release`, `default_release`, `release`,
`nvim_version`, `target`, `bundled`, `python`, `python_arch`, `emulated`,
`cache`, `prefix`, `installed` (`[release, platform]` pairs), `releases` (per
line: `nvim_version`, `platforms`, `bundled`) and `vendored_targets`
(`"<release>/<platform>"` labels). `path` reports `release`, `nvim_version`,
`target`, `prefix`, `exe`, `installed`, `managed`, `bundled`, `bundle` and
`installed_releases`. `path --installed-only` exits 1 unless `installed` is
true, which is the check to use in a script.

## Environment variables

`FAST_NVIM_RELEASE_CACHE_DIR` is the only variable that changes where the tool
keeps its files:

| Variable | Effect |
| --- | --- |
| `FAST_NVIM_RELEASE_CACHE_DIR` | Where downloaded archives and extracted builds are kept. Default: `~/.cache/fast-nvim-release`, or `$XDG_CACHE_HOME/fast-nvim-release` when that is set. Point it at a bigger disk or a shared directory when your home cache is small or read-only. |

`NO_COLOR` turns off the coloured `error:`/`warning:` prefixes when set to a
non-empty value, as [no-color.org](https://no-color.org) describes;
`FORCE_COLOR` does the opposite and keeps colour when stdout is not a terminal.
`NO_COLOR` wins if both are set.

There is no prefix variable: `setup` takes its location only from `--prefix`.
`PATH` and `LOCALAPPDATA` are read to word messages and pick the default for
`path`/`version`, but the tool never writes them.

## Contributing

Bugs, build details and how to add a Neovim release are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

fast-nvim-release is MIT licensed. The vendored Neovim binaries and runtime are
distributed under Neovim's license (Apache-2.0, plus the Vim license for parts
of the runtime); see `THIRD_PARTY_NOTICES.md` and the
`fast_nvim_release/_nvim/LICENSE.nvim-<release>.txt` files inside the wheel.
