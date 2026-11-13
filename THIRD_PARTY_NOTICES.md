# Third-party notices

## Neovim

This package redistributes unmodified builds of
[Neovim](https://github.com/neovim/neovim) from its official GitHub releases,
and, in the wheels for older Linux systems, from
[`neovim/neovim-releases`](https://github.com/neovim/neovim-releases), where the
Neovim project publishes builds for glibc 2.17.

The release lines shipped are pinned in `src/fast_nvim_release/platforms.py`
(`RELEASES`). Each release line is pinned to one patch release:

| Release | Neovim version |
| --- | --- |
| 0.10 | v0.10.4 |
| 0.11 | v0.11.7 |
| 0.12 | v0.12.6 |

* Neovim: <https://github.com/neovim/neovim>
* License: Apache License 2.0, except for parts contributed under the Vim
  license. The full text for each release line ships as
  `fast_nvim_release/_nvim/LICENSE.nvim-<release>.txt` (fetched from that
  release's tag by `scripts/fetch_nvim.py`), and the Vim license is also present
  in the runtime as `share/nvim/runtime/doc/uganda.txt`.
* Each vendored build is extracted from the release archive whose sha256 is
  pinned in `platforms.py`.

## Bundled third-party components

Neovim's release archives include third-party code and assets (tree-sitter
parsers, Lua libraries, syntax files and colorschemes, and on Windows the
`libuv`/`LuaJIT` DLLs and the `win32yank`, `xxd` and `tee` helper executables).
Their licenses are documented by the Neovim project in the `doc/` tree of the
runtime and in the upstream repositories of those components.

No changes are made to the vendored binaries: `scripts/fetch_nvim.py` extracts
`bin/`, `lib/nvim/` and `share/nvim/runtime/` from each published archive.

## fast-nvim-release itself

MIT licensed; see `LICENSE`.
