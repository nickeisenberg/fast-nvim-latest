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

`requirements-ci.txt` is the one list of tool pins for `ci.yml` and
`release.yml`. Actions in the workflows are pinned to commit SHAs with the tag
in a comment; bump both together.

## Layout

```
src/fast_nvim_release/platforms.py  releases, platforms, wheels and pinned checksums
src/fast_nvim_release/bundle.py     find the bundled builds, install into a prefix
src/fast_nvim_release/cli.py        setup / path / run
setup.py                            tags the wheel with its platform
scripts/fetch_nvim.py               download, verify and unpack builds into the package
scripts/build_wheels.py             build every wheel in platforms.WHEELS
scripts/check_wheels.py             validate wheels before upload
scripts/smoke_test.py               install a wheel in a clean venv and use it
scripts/check_upstream.py           is every pin still the newest patch? (both repos)
```

## Building wheels

A wheel can only carry one platform's executables, so each version publishes
eight wheels with the same version and different platform tags (the scheme
`ruff` and `uv` use), all listed in `platforms.WHEELS`:

- six with Neovim's official builds (`RELEASES`), one per platform;
- two Linux wheels for older systems, tagged
  `manylinux_2_17_*.manylinux2014_*`, with the glibc 2.17 builds from
  `neovim/neovim-releases` (`OLD_GLIBC_RELEASES`). pip prefers the
  `manylinux_2_34` wheel wherever it can run, so these only reach systems the
  official builds cannot start on. The `manylinux2014` alias lets pip < 20.3
  (RHEL 8's python39) recognise them.

```console
$ python scripts/fetch_nvim.py                  # vendor this machine's builds
$ python -m build --wheel                       # one wheel for this machine
$ python scripts/build_wheels.py --all          # all eight, from any machine
$ python scripts/build_wheels.py --wheel manylinux_2_17_x86_64.manylinux2014_x86_64
$ python scripts/check_wheels.py                # validate dist/*.whl
$ python scripts/smoke_test.py [WHEEL]          # install a wheel and use it
```

`fetch_nvim.py` caches archives in `~/.cache/fast-nvim-release`
(`FAST_NVIM_RELEASE_CACHE_DIR`), checks them against the sha256 pinned in
`platforms.py` every time, and unpacks only `bin/`, `lib/nvim/` and
`share/nvim/runtime/` into `src/fast_nvim_release/_nvim/<release>/<platform>/`
(git-ignored), with a `VERSION` file naming the build's Neovim version (the
old-glibc wheels can carry an older patch than the main pin). The per-release
`LICENSE.nvim-<release>.txt` files there are tracked, because every wheel must
ship them.

## Adding or bumping a release

1. `python scripts/check_upstream.py` prints the newest patch of each line,
   for both `neovim/neovim` and `neovim/neovim-releases`.
   (`.github/workflows/upstream.yml` runs it with `--strict` every Monday.)
2. Update `RELEASES` in `platforms.py`: the version, and the sha256 of each
   platform's archive from the release's `.sha256sum` file or GitHub's asset
   digest. Leave out a platform Neovim does not publish, or whose build would
   raise the wheel's platform tag. Update `OLD_GLIBC_RELEASES` the same way
   once `neovim-releases` publishes the new patch; until then it may stay a
   patch behind (`check_upstream.py` prints a note, not a failure).
3. If it is a new minor line, check its glibc and macOS floor still fit the
   tags in `PLATFORMS` (`check_wheels.py` checks glibc automatically; for macOS,
   `otool -l bin/nvim | grep -A3 LC_BUILD_VERSION` shows `minos`).
4. `python scripts/build_wheels.py --all && python scripts/check_wheels.py`
   (it fails if an old-glibc binary needs more than glibc 2.17).

## Releasing

`.github/workflows/release.yml` publishes on a `v*` tag push, via PyPI trusted
publishing:

```console
$ git tag -a v0.3.0 -m "fast-nvim-release 0.3.0"
$ git push origin v0.3.0
```

The tag must equal `fast_nvim_release.__version__`; the release job fails the
upload otherwise. A manual `workflow_dispatch` run with `publish: true`
rehearses against TestPyPI only. PyPI uploads are permanent.

## License

By contributing you agree your changes are MIT licensed, like the rest of the
project. Vendored Neovim binaries keep their own licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
