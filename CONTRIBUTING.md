# Contributing to fast-nvim-release

## Getting started

```console
$ git clone git@github.com:nickeisenberg/fast-nvim-release.git
$ cd fast-nvim-release
$ python -m pytest                 # the whole suite, no network needed
$ python -m ruff check .           # lint (CI runs this)
$ python -m ruff format --check .
```

The test suite runs against fake vendored builds, so it needs no Neovim
downloads and works offline.

## Vendoring Neovim

The wheels carry real Neovim binaries, so a build needs them on disk first:

```console
$ python scripts/fetch_nvim.py                        # every release, this platform
$ python scripts/fetch_nvim.py --release 0.12         # one release line
$ python scripts/fetch_nvim.py --platform windows     # cross-vendor for Windows
$ python scripts/fetch_nvim.py --all                  # every platform
$ python scripts/fetch_nvim.py --list                 # what is vendored/cached
```

Archives are downloaded once into `~/.cache/fast-nvim-release/` (override with
`FAST_NVIM_RELEASE_CACHE_DIR`) and verified against the sha256 pinned in
`platforms.py`.

Then build:

```console
$ python -m build --wheel                       # one wheel for this platform
$ python scripts/build_wheels.py --all          # all six
$ python scripts/build_wheels.py --platform macos-arm64 --offline
$ python scripts/check_wheels.py                # validate dist/*.whl
$ python scripts/smoke_test.py                  # install a wheel in a clean venv and use it
```

Cross-builds need `FAST_NVIM_RELEASE_PLAT_TAG`, which `build_wheels.py` sets for
you.

## Layout

```
src/fast_nvim_release/cli.py        setup / path / version / run
src/fast_nvim_release/bundle.py     vendored bundles, cache, extraction, installation
src/fast_nvim_release/platforms.py  release + platform matrix (single source of truth)
setup.py                            platform wheel tags, refuses empty/mislabelled wheels
scripts/fetch_nvim.py               download/extract release archives into the package
scripts/build_wheels.py             build all six wheels from one machine
scripts/check_wheels.py             validate wheels (tag vs. binaries, runtime, licences)
scripts/check_upstream.py           compare the pins with Neovim's own releases
scripts/smoke_test.py               install a wheel in a clean venv and use it
```

Vendored trees live at `src/fast_nvim_release/_nvim/<release>/<platform>/`, and
each release's `BUILD_INFO.json` records the archive sha256 it came from. Those
trees are git-ignored; the per-release `LICENSE.nvim-<release>.txt` files are
tracked, because every wheel must ship them.

## Why one wheel per platform

A wheel can only contain one platform's executables, so each release publishes
**six wheels with the same version and different platform tags**:

```
fast_nvim_release-0.2.1-py3-none-manylinux_2_34_x86_64.whl   35.9 MB  3 releases
fast_nvim_release-0.2.1-py3-none-manylinux_2_34_aarch64.whl  24.3 MB  2 releases (no 0.10 arm64)
fast_nvim_release-0.2.1-py3-none-macosx_11_0_x86_64.whl      31.5 MB  3 releases
fast_nvim_release-0.2.1-py3-none-macosx_11_0_arm64.whl       30.6 MB  3 releases
fast_nvim_release-0.2.1-py3-none-win_amd64.whl               36.4 MB  3 releases
fast_nvim_release-0.2.1-py3-none-win_arm64.whl               25.9 MB  2 releases (no 0.10)
```

Each wheel carries its releases for one platform (~100 MiB installed), so
`pip install` is one download for everything. The largest wheel is 36 MB,
well inside PyPI's 100 MB per-file limit; a full release uploads ~183 MB against
a 10 GB project limit. See
<https://docs.pypi.org/project-management/storage-limits/>.

This is the same one-wheel-per-platform scheme `ruff` and `uv` use. To shrink a
wheel, vendor only some releases before building
(`python scripts/fetch_nvim.py --release 0.12`); the others then come from GitHub
on first use. `check_wheels.py` accepts a partial set, as long as the tagged
platform's builds are present and complete.

## Staying up to date

`platforms.py` is a hand-maintained copy of what Neovim publishes, so something
has to notice when upstream moves:

```console
$ python scripts/check_upstream.py             # report
$ python scripts/check_upstream.py --strict    # exit non-zero unless every pin is current
```

It asks GitHub for Neovim's release list and checks two things:

- each pinned version is the newest patch of its release line, and a release
  line newer than the newest one supported here is called out;
- each pinned `(sha256, size)` matches the checksum Neovim published for that
  archive — from the asset's digest, or from the release's own `.sha256sum`
  file for archives uploaded before GitHub recorded digests (all of 0.10.x).
  Only a few hundred bytes are downloaded, never an archive.

A wrong checksum or size always fails; merely being behind upstream fails only
with `--strict`. Set `GITHUB_TOKEN` to lift the anonymous API rate limit.

`.github/workflows/upstream.yml` runs the strict check every Monday, so a stale
pin shows up as a red build instead of as a user noticing `setup 0.12` installs
an old patch. The live tests in `tests/test_upstream.py` ask GitHub for real
and are skipped unless `FAST_NVIM_RELEASE_NETWORK_TESTS=1` is set — the rest of
the suite stays offline:

```console
$ FAST_NVIM_RELEASE_NETWORK_TESTS=1 python -m pytest tests/test_upstream.py
```

## Adding or bumping a release

0. `python scripts/check_upstream.py` prints the newest tag per release line;
   that is what step 1 should pin.
1. Add or update the entry in `RELEASES` in `platforms.py`: the pinned `version`,
   then `(sha256, size)` per platform. Take the checksum from the release's own
   `.sha256sum` file or GitHub's asset digest, and verify it against a local
   download.
2. Check the new binary's glibc / macOS floor if it is a different minor line.
   A wheel carries several releases under one tag, so the tag must stay true for
   all of them. `check_wheels.py` recomputes this from the binaries and fails if
   a payload needs a newer glibc than its tag promises.
   - `readelf -V bin/nvim | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1`
   - On macOS, `check_wheels.macho_min_os` reads the load commands directly.
3. If a release never published a platform (0.10 has no Windows-on-ARM build),
   list it in `missing`. If a build exists but would raise the wheel tag, list it
   in `downloaded_not_bundled` instead — that is why 0.10 on linux-arm64 is
   fetched on demand: it needs glibc 2.38 while 0.11/0.12 need 2.34, and
   vendoring it would push the arm64 wheel to `manylinux_2_38` and drop Ubuntu
   22.04, Debian 12 and RHEL 9.
4. `python scripts/check_upstream.py --strict`: the new pin must be the newest
   patch and its checksums must match Neovim's published ones.
5. `python scripts/fetch_nvim.py --all --force`, then
   `python scripts/build_wheels.py --all` and `python scripts/check_wheels.py`.

## Releasing

`.github/workflows/release.yml` publishes on a `v*` tag, via PyPI trusted
publishing (no tokens):

```console
$ git tag -a v0.3.0 -m "fast-nvim-release 0.3.0"
$ git push origin v0.3.0
```

The tag must equal `fast_nvim_release.__version__`; the release job checks this
and `check_wheels.py --expect-version` fails the upload otherwise. A manual
`workflow_dispatch` run with `publish: true` rehearses against TestPyPI without
touching PyPI.

PyPI uploads are permanent: a published version cannot be replaced or edited,
only superseded. Use TestPyPI first if anything about the payload changed.

## License

By contributing you agree your changes are MIT licensed, like the rest of the
project. Vendored Neovim binaries keep their own licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
