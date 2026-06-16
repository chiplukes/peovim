# Peovim

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

A fast, modern, cross-platform modal text editor written in Python — heavily inspired by Neovim with a clean plugin API designed for extensibility.

I've been a Vim/Neovim editor Nerd for a long time and this project grew out of the question: 

* "Can a basic Vim/Neovim style modal editor be created entirely with Python?"

Achieving basic functionality was surprisingly straightforward, but then I immediately had these follow up questions:

* Can I start adding all the plugins/features that I use in Neovim?
* How slow will this be with this built using Python?

It turns out that most of the things that I wanted were relatively simple to add, especially with AI assistance.  Additional features should be easy to add via the plugin API.

An optional Cython based renderer was added as a performance improvement.

Being written entirely in Python this editor is very easy to modify or create plugins for (especially with AI tools).  If you are a modal editor nerd, fork this project and create your own hyper customized modal editor!.

![Peovim demo](demo.gif)

## Features

- **Modal editing** — Normal, Insert, Visual, and Visual Block modes with Vim-compatible bindings
- **LSP support** — go to definition, hover, references, rename, code actions, completions, diagnostics, inlay hints
- **Syntax highlighting** — tree-sitter powered, covering Python, JS/TS, Rust, Go, C/C++, Lua, and more
- **Side/Bottom Panels** — file explorer, document outline, diagnostics, references, workspace symbols, git status
- **Git integration** — status panel with staging/unstaging/discarding, branch management, diff view, log browser
- **Fuzzy finder** — file picker, buffer picker, live grep, command palette
- **Session management** — autosave/restore with per-project state
- **Flash jump** — 2-char jump labels (`s` in normal mode)
- **Which-key** — similar to Folke's key hint overlay Neovim plugin (my personal favorite Neovim plugin!)
- **Marker groups** — persistent bookmarks with annotations and gutter signs
- **Local history** — per-file timestamped save history
- **Copilot** — support for Copilot autocompletions.
- **Plugin API** — plain Python plugins with event hooks, keymaps, commands, decorations, and sidebar panels

## Features more unique to my (Verilog, Python) workflow
- **Verilog LSP** — a custom LSP that allows heirarchy collapse/extract, signal tracing, and more features using [verilog-tools](https://github.com/chiplukes/verilog-parser)

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

### From source (development)

```bash
git clone https://github.com/chiplukes/peovim
cd peovim
uv sync
```

### As a global tool

```bash
uv tool install .
```

After install, the `peovim` command is available on your PATH:

```bash
peovim              # open empty buffer
peovim file.py      # open a file
```

### Updating

Pull the latest changes, then reinstall:

```bash
git pull
uv tool install --reinstall .
```

## Running (development)

```bash
uv run peovim           # open empty buffer
uv run peovim file.py   # open a file
```

## Configuration

Create `init.py` at the platform config path:

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\peovim\init.py` |
| Linux    | `~/.config/peovim/init.py` |
| macOS    | `~/Library/Application Support/peovim/init.py` |

The config file is plain Python. Example:

```python
# init.py

plugins.load("peovim.plugins.lsp")
plugins.load("peovim.plugins.picker")
plugins.load("peovim.plugins.explorer")
plugins.load("peovim.plugins.outline")
plugins.load("peovim.plugins.gitsigns")
plugins.load("peovim.plugins.diagnostics_panel")
plugins.load("peovim.plugins.formatter")
plugins.load("peovim.plugins.local_history")

# Space as leader (default is backslash)
options.set("leader", " ")
options.set("number", True)
options.set("relativenumber", True)
options.set("tabstop", 4)

# Custom keybindings
keymap.nmap("<leader>w", ":w<CR>", desc="Save file")
```

Project-local config at `.peovim/init.py` is also supported — the editor prompts for trust on first encounter.

A more thorough `init.py` can be found here:

[My Init.py](https://github.com/chiplukes/.dotfiles/blob/master/.config/peovim/init.py)

## Key Bindings

### Navigation

| Key | Action |
|-----|--------|
| `<leader>ff` | Fuzzy find files |
| `<leader>fg` | Live grep |
| `<leader>fb` | Open buffers |
| `<leader>sw` | Grep word under cursor |
| `<C-o>` / `<C-i>` | Jump back / forward |
| `gf` | Go to file under cursor |
| `s` | Flash jump (type 2 chars) |

### LSP

| Key | Action |
|-----|--------|
| `gd` | Go to definition |
| `K` | Hover documentation |
| `<leader>gr` | References sidebar |
| `<leader>rn` | Rename symbol |
| `<leader>ca` | Code actions |
| `]d` / `[d` | Next / previous diagnostic |
| `<leader>o` | Document outline sidebar |
| `<leader>cD` | Diagnostics sidebar |
| `<leader>csw` | Workspace symbols sidebar |

### Sidebar

| Key | Action |
|-----|--------|
| `<leader>e` | Toggle file explorer |
| `<leader>gs` | Toggle git panel |
| `<A-h>` | Focus sidebar from editor |
| `<A-l>` | Return to editor from sidebar |
| `<A-j>` / `<A-k>` | Cycle sidebar panels |
| `<Esc>` | Hide sidebar |

### Git Panel (when focused)

| Key | Action |
|-----|--------|
| `a` | Stage file |
| `u` | Unstage file |
| `x` | Discard changes |
| `d` | Diff file against HEAD |
| `l` | Git log browser |
| `P` | Push |
| `p` | Pull |

### Editing

| Key | Action |
|-----|--------|
| `gcc` | Toggle comment |
| `ysiw{char}` | Surround inner word |
| `cs{old}{new}` | Change surrounding |
| `ds{char}` | Delete surrounding |
| `<leader>pr` | Paste from yank register |
| `ga` (visual) | Align on character |

See [`notes/keys.md`](notes/keys.md) for the full key binding reference.

## Ex Commands

| Command | Description |
|---------|-------------|
| `:w` / `:wq` / `:q` | Save / save+quit / quit |
| `:e <file>` | Open file |
| `:split` / `:vsplit` | Split window |
| `:LspInfo` / `:LspRestart` | LSP status / restart |
| `:format` | Format buffer |
| `:colorscheme <name>` | Switch theme (`catppuccin`, `gruvbox`, `onedark`) |
| `:Session` / `:SessionLoad` | Save / load session |
| `:GitLog` | Open git log browser |
| `:checkhealth` | Run health checks |

Press `<Tab>` in the command line to fuzzy-browse all available commands.

## Themes

Built-in themes: `catppuccin` (default), `gruvbox`, `onedark`.

Switch with `:colorscheme <name>` or set in `init.py`:

```python
options.set("colorscheme", "gruvbox")
```

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest tests/ --tb=no -q

# Lint / format
uv run ruff check peovim/
uv run ruff format peovim/
```

### Optional: native Cython renderer

A Cython-accelerated renderer is included for a ~4–25× speedup on the render path. It builds automatically if a C compiler is present (`cl.exe` on Windows, `gcc`/`clang` on Linux/macOS). The pure-Python fallback is used otherwise — the editor runs fine either way.

```bash
# Verify which renderer is active
uv run python -c "from peovim._native import HAS_NATIVE; print(HAS_NATIVE)"
```

## Documentation

- [`notes/getting_started.md`](notes/getting_started.md) — install, configuration, and getting started
- [`notes/keys.md`](notes/keys.md) — complete key binding reference
- [`notes/user_guide.md`](notes/user_guide.md) — day-to-day usage guide
- [`notes/architecture.md`](notes/architecture.md) — internals and architecture
- [`notes/developer_guide.md`](notes/developer_guide.md) — plugin API and contribution guide

## Acknowledgments

I have spent an embarassing amount of time over the years "Configuring" Vim (and then Neovim).  The plugin and editor developers are truly amazing and I strongly suggest spending time in that community to see all the creative and amazing things that are possible!

The following list was inspiration for this project:
- [Neovim](https://neovim.io/):
- [flash.nvim](https://github.com/folke/flash.nvim) — flash jump
- [snacks.nvim](https://github.com/folke/snacks.nvim) — dashboard, notifications, indent guides
- [gitsigns.nvim](https://github.com/lewis6991/gitsigns.nvim) — git gutter and status
- [mini.nvim](https://github.com/echasnovski/mini.nvim) — picker, surround, commentary
- [blink.cmp](https://github.com/Saghen/blink.cmp) — LSP completion
- [conform.nvim](https://github.com/stevearc/conform.nvim) — formatting
- [which-key.nvim](https://github.com/folke/which-key.nvim) — key hint overlay
- [todo-comments.nvim](https://github.com/folke/todo-comments.nvim) — TODO highlights
- [persistence.nvim](https://github.com/folke/persistence.nvim) — session management
- [nvim-lspconfig](https://github.com/neovim/nvim-lspconfig) — LSP server configuration
