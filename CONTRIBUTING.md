# Contributing to fast-nvim-release

## Getting started

```console
$ git clone git@github.com:nickeisenberg/fast-nvim-release.git
$ cd fast-nvim-release
$ python -m pip install -r requirements-ci.txt   # the pinned tools CI uses
$ python -m pytest                 # the whole suite, offline, fake Neovim builds
$ python -m ruff check .
$ python -m ruff format --check .
```

`requirements-ci.txt` is the one list of tool pins for the workflows. Actions
are pinned to commit SHAs with the tag in a comment; bump both together.

## Layout

```
lines.json                          every Neovim line: release counter k, pinned builds
src/fast_nvim_release/cli.py        setup / path / run  (the stable interface)
src/fast_nvim_release/bundle.py     the bundled build, installing into a prefix
src/fast_nvim_release/platforms.py  platforms, wheel tags, which machine this is
setup.py                            versions and tags the wheel from _nvim/build.json
scripts/lines.py                    lines.json -> the list of wheels to build
scripts/fetch_nvim.py               download, verify and unpack one wheel's build
scripts/build_wheels.py             build any set of wheels (or only unpublished ones)
scripts/check_wheels.py             validate wheels against lines.json
scripts/smoke_test.py               install wheels in a clean venv and use them
scripts/pins.py                     follow Neovim's releases; bump k
scripts/release_notes.py            one GitHub release per published version
```

## Versions and lines

A package version is `<line>.<k>`: one Neovim line, and that line's release
counter `k` from `lines.json`. Each line carries the newest patch of its
Neovim line: the official build (`neovim/neovim`) for six platforms, plus,
where Neovim publishes one, the glibc 2.17 build (`neovim/neovim-releases`)
for older Linux, in wheels tagged `manylinux_2_17_*.manylinux2014_*`. pip
prefers the `manylinux_2_34` wheel wherever it can run.

`k` goes up whenever anything in that line's wheels changes. Nothing is ever
deleted from PyPI, and a release only publishes versions the index does not
have yet, so bumping `k` is how a line gets released and every other line is
left alone.

The command line is a promise (see "Stability" in the README): versions name
Neovim lines, so they cannot signal breaking changes. Add, never remove.

## Building wheels locally

```console
$ python scripts/fetch_nvim.py                  # newest line, the wheel pip picks here
$ python scripts/fetch_nvim.py --line 0.11      # another line
$ pip install .                                 # or: python -m build --wheel
$ python scripts/build_wheels.py --wheel host   # every line's wheel for this machine
$ python scripts/build_wheels.py --all          # every wheel of every line
$ python scripts/check_wheels.py                # validate dist/*.whl
$ python scripts/smoke_test.py dist/*.whl       # install them and use them
```

`fetch_nvim.py` unpacks one build into `src/fast_nvim_release/_nvim/` (git
ignores it) with a `build.json` naming the version, Neovim build and wheel tag,
which `setup.py` reads. On a Linux older than glibc 2.34 the default is the
old-glibc wheel, so the same two commands work there. Archives are cached in
`~/.cache/fast-nvim-release` (`FAST_NVIM_RELEASE_CACHE_DIR`) and checked against
`lines.json` every time. The `LICENSE.nvim-<line>.txt` files in `_nvim/` are
tracked, because every wheel must ship its line's license.

## When Neovim ships a patch (or a new line)

`.github/workflows/upstream.yml` runs `python scripts/pins.py update` every
Monday. It moves each pin to the newest patch in its repository with the
checksums GitHub publishes, adds new lines, bumps `k` of every changed line,
and opens a pull request (branch `pins/update`) with CI started on it. Review
`lines.json`, wait for CI, merge, then release.

By hand: `python scripts/pins.py check` reports, `python scripts/pins.py update`
edits `lines.json` the same way.

Check a new line's glibc and macOS floor: `check_wheels.py` fails if a Linux
build needs a newer glibc than its tag, and for macOS
`otool -l bin/nvim | grep -A3 LC_BUILD_VERSION` shows `minos` (the tags promise
11.0). A build that cannot meet its tag goes in that build's `skip` list in
`lines.json` (0.10's arm64 build needs glibc 2.38, for example).

## When our code changes

Only changes to the installed package (`src/fast_nvim_release/`) need a
release; build scripts, CI and docs do not. Batch fixes, then:

```console
$ python scripts/pins.py bump --all     # every line gets k + 1
```

commit, merge, and release.

## Releasing

Actions > release > Run workflow, choosing the index (TestPyPI by default).
It builds every version in `lines.json` that index does not have, validates
and smoke-tests them, and uploads them. Publishing to PyPI also creates one
GitHub release per version, listing the exact Neovim in each wheel. An index
never accepts the same version twice; rehearse on TestPyPI first.

One-time setup on each index (Your account > Publishing > add a pending
publisher, or the project's Publishing settings): repository
`nickeisenberg/fast-nvim-release`, workflow `release.yml`, environment
`testpypi` or `pypi` (matching the GitHub environments of the same names). The
weekly bot also needs Settings > Actions > General > "Allow GitHub Actions to
create and approve pull requests".

## License

By contributing you agree your changes are MIT licensed, like the rest of the
project. Vendored Neovim binaries keep their own licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
