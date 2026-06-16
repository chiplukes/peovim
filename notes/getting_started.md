# Getting Started

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for Python package management.

### As a standalone tool (recommended for regular use)

Installs peovim into an isolated environment and adds `peovim` to your `PATH`:

```bash
git clone <repo-url>
cd peovim
uv tool install .
```

With optional performance extras (`pygit2` for faster git, `jedi` for offline Python
completions):

```bash
uv tool install ".[fast]"
```

After a tool install, run the editor directly — no `uv run` prefix needed:

```bash
peovim
peovim file.py
```

To upgrade after pulling new changes:

```bash
uv tool install --reinstall .
```

### As a development dependency (for contributors)

```bash
git clone <repo-url>
cd peovim
uv sync --extra dev
```

## Running the editor (development)

```bash
uv run peovim          # open an empty buffer
uv run peovim file.py  # open a specific file
```

## Native renderer (optional Cython acceleration)

The editor ships a Cython-accelerated cell-grid renderer that collapses the
`flush() → list[RenderOp] → ANSI encode` pipeline into a single C pass, giving
a ~4–25× speedup on the render hot path depending on how many cells changed.
The pure-Python fallback is used automatically when the extension is absent, so
this step is entirely optional.

### How it builds

Cython is declared as a build-system dependency, so a standard sync is all
that is needed when a C compiler is available:

```bash
uv sync          # builds the extension automatically on first install
```

If no C compiler is found the build prints a warning and continues — the editor
runs on pure Python without any further action required.

### Rebuilding after changing `.pyx` source

`uv sync` only compiles the extension when it does not already exist.  After
editing `peovim/_native/cell_grid.pyx`, trigger a rebuild manually:

```bash
uv run python setup.py build_ext --inplace   # fastest
# or
uv sync --reinstall-package peovim               # goes through uv
```

### Verifying the active renderer

From the command line:

```bash
uv run python -c "from peovim._native import HAS_NATIVE; print(HAS_NATIVE)"
# True  → native Cython extension is loaded
# False → running pure Python
```

From inside the editor, start with `--log` and the renderer mode is logged at
startup:

```bash
uv run peovim somefile.py --log
# look for:  INFO peovim.main — renderer: native (Cython)
#        or: INFO peovim.main — renderer: pure Python
```

## Documentation map

- [architecture.md](architecture.md) — architecture deep dive and current review notes
- [getting_started.md](getting_started.md) — install, config, and common first steps
- [user_guide.md](user_guide.md) — day-to-day editor usage guide
- [developer_guide.md](developer_guide.md) — internals, extension points, and contribution guide

## User Configuration

Create a config file at the platform-appropriate location:

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\peovim\init.py` (usually `C:\Users\<you>\AppData\Roaming\peovim\init.py`) |
| Linux    | `~/.config/peovim/init.py` |
| macOS    | `~/Library/Application Support/peovim/init.py` |

The config file is plain Python. Sub-APIs (`keymap`, `options`, `ui`, etc.) are injected
into the namespace directly:

```python
# Example init.py

# Explicitly choose the plugins you want.
plugins.load("peovim.plugins.picker")
plugins.load("peovim.plugins.explorer")
plugins.load("peovim.plugins.diagnostics_panel")
plugins.load("peovim.plugins.lsp")
plugins.load("peovim.plugins.editor_utils")
plugins.load("peovim.plugins.local_history")

# Change the leader key (default is backslash \)
options.set("leader", " ")   # use Space as leader

# Add custom keybindings — remap any <Plug> to a different key
keymap.nmap("<leader>w", ":w<CR>", desc="Save file")
keymap.nmap("K", "<Plug>LspHover", desc="Hover docs")

# Remap a plugin-defined <Plug> name
keymap.nmap("<leader>ca", "<Plug>LspCodeAction", desc="Code action")

# Give the sidebar its own background while keeping theme-derived headers.
ui.set_sidebar_style(background="#252526")

# Load extra plugins
plugins.load("my_project.my_plugin")
```

[Link to my personal init.py](https://github.com/chiplukes/.dotfiles/blob/master/.config/peovim/init.py)


Built-in plugins are no longer auto-loaded at startup. Your `init.py` is now the
place where you choose which built-ins to enable.

Project-local `.peovim/init.py` is supported. On first encounter, `peovim` prompts for a
trust decision and persists it in shada. Untrusted project configs are skipped.

Load plugins before adding mappings or commands that depend on their `<Plug>`
names or helper functions.

The persistent sidebar uses the theme background by default. You can override its
body background with `ui.set_sidebar_style(background="#252526")`. Sidebar headers
resolve `sidebar.header.active` and `sidebar.header.inactive` from the active theme
when those groups exist, and otherwise fall back to the existing built-in colors.

## Leader Key

The default leader key is `\` (backslash). All `<leader>` bindings require pressing
backslash first, then the rest of the sequence.

To use Space as leader (common Neovim setup), add `options.set("leader", " ")` to your
`init.py` before any plugin/keymap calls.

---

## Built-in Key Bindings

See [keys.md](keys.md) for the complete keybinding reference, including default bindings,
`<Plug>` names, and remapping examples.

---

## Persistence and multi-instance notes

- Global editor state such as recent files, command history, search history, and marks is persisted across runs.
- File saves now use atomic replace and stop on normal save if the on-disk file changed externally; use `:e` to reload or `:w!` to overwrite.
- Running multiple editor instances at the same time is supported, but shared persisted state is mostly last-writer-wins.
- `shada`, sessions, and plugin stores now use atomic replace, but they still do not merge concurrent updates.
- Opening the same project in multiple instances is usually fine for editing, and project-local helper data such as `.peovim/markers.json` and git compare snapshots under `.peovim/` now avoid partial writes with atomic replace, but they should still be treated as single-writer friendly rather than strongly coordinated.
- Session restore/save is best treated as a convenience feature, not yet a fully conflict-aware workspace database.

For architecture and implementation details, see [architecture.md](architecture.md).

---

## Options

Set in `init.py` via `options.set(name, value)`.

| Option | Default | Description |
|--------|---------|-------------|
| `leader` | `\` | Leader key character |
| `number` | `false` | Show line numbers |
| `relativenumber` | `false` | Show relative line numbers |
| `tabstop` | `4` | Tab width |
| `expandtab` | `true` | Use spaces instead of tabs |
| `scrolloff` | `4` | Minimum lines above/below cursor |
| `colorcolumn` | `""` | Column(s) to highlight (e.g. `"80,120"`) |
| `signcolumn` | `"auto"` | `"yes"` always show, `"auto"` only when signs present |
| `cursorblink` | `false` | Use a blinking terminal cursor instead of a steady painted block |
| `insertcursor` | `"block"` | Insert-mode cursor shape: `"block"` or `"bar"` |
| `indentguides` | `"none"` | `"normal"` to show indent guides |
| `hlsearch` | `true` | Highlight search matches |
| `ignorecase` | `false` | Case-insensitive search |
| `smartcase` | `false` | Override ignorecase when pattern has uppercase |
| `autoindent` | `true` | Copy indent from previous line on Enter |

---

## Troubleshooting

### Native extension not building

If `uv sync` prints a warning about the native extension and `HAS_NATIVE` is
`False`, check that a C compiler is on `PATH`:

- **Windows**: install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  and make sure the "Desktop development with C++" workload is selected, then
  open a Developer Command Prompt (or run `vcvars64.bat`) before syncing.
- **Linux/macOS**: install `gcc` / `clang` via your package manager.

Once the compiler is available, rebuild:

```bash
uv run python setup.py build_ext --inplace
```

### Syntax highlighting not working

Tree-sitter grammars are in the main dependencies, so a plain `uv sync` installs them.
If highlighting is missing for a specific file type, check that the filetype is supported
(Python, JS/TS, Rust, C, C++, Go, Lua, JSON, YAML, TOML, Markdown, Bash, Verilog).
For unsupported types the editor falls back to no highlighting — no error is shown.

### `<leader>` bindings not firing

The default leader is `\` (backslash). Press backslash then the rest of the sequence.
To change it: `options.set("leader", " ")` in `init.py`.

### Running tests

```bash
uv run pytest tests/ --tb=no -q        # pass/fail summary
uv run pytest tests/ --tb=short -q    # with failure details
```

### Lint / format

```bash
uv run ruff check peovim/
uv run ruff format peovim/
```

### Logs

Debug logs are written to `~/.config/peovim/peovim.log` on Linux/macOS and
`C:\Users\<you>\.config\peovim\peovim.log` on Windows (5 MB rotating, 2 backups).

Logs are off by default. Enable from inside the editor:

```
:LogOn
:LogOn modules=peovim.ui.event_loop level=debug
:LogView
```
