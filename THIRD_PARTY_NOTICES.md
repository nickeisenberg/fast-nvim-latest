# Third-party notices

## Neovim

This package redistributes unmodified builds of
[Neovim](https://github.com/neovim/neovim) from its official GitHub releases.

* Neovim: <https://github.com/neovim/neovim>
* Release used: see `NVIM_VERSION` in `src/fast_nvim/platforms.py` and
  `fast_nvim/_nvim/BUILD_INFO.json` inside the wheel.
* License: Apache License 2.0, except for parts contributed under the Vim
  license. The full text is shipped as `fast_nvim/_nvim/LICENSE.nvim.txt`
  (fetched from the pinned release tag by `scripts/fetch_nvim.py`), and the
  Vim license is also present in the runtime as
  `share/nvim/runtime/doc/uganda.txt`.

## Bundled third-party components

Neovim's release archives include third-party code and assets (tree-sitter
parsers, Lua libraries, syntax files and colorschemes, and on Windows the
`libuv`/`LuaJIT` DLLs and the `win32yank`, `xxd` and `tee` helper executables).
Their licenses are documented by the Neovim project in the `doc/` tree of the
runtime and in the upstream repositories of those components.

No changes are made to the vendored binaries: `scripts/fetch_nvim.py` extracts
`bin/`, `lib/nvim/` and `share/nvim/runtime/` from the published archive and
records the archive's sha256 in `BUILD_INFO.json`.

## fast-nvim itself

MIT licensed; see `LICENSE`.
