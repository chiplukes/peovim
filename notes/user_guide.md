# User Guide

This guide is for day-to-day editor use after installation. For setup details, see [getting_started.md](getting_started.md). For internals, see [developer_guide.md](developer_guide.md).

## What `peovim` is

`peovim` is a modal terminal editor inspired by Neovim, with a Python implementation, a custom terminal UI, and a plugin API designed for long-term extensibility.

It is strongest today in these areas:
- modal editing
- split/tab workflows
- tree-sitter syntax highlighting
- LSP navigation and diagnostics
- fuzzy finding and sidebars
- diff workflows, marker groups, and project navigation

---

## Installation

### As a standalone tool (recommended for regular use)

`uv tool install` installs peovim into an isolated environment and puts the `peovim`
command on your `PATH` — no `uv run` prefix needed after that.

From a local clone:

```bash
git clone <repo-url>
cd peovim
uv tool install .
```

With optional performance extras (faster git queries via `pygit2`, offline Python
completions via `jedi`):

```bash
uv tool install ".[fast]"
```

Then just run:

```bash
peovim
peovim path/to/file.py
```

To upgrade after pulling new changes:

```bash
uv tool install --reinstall .
```

### As a development dependency (for contributors)

```bash
uv sync --extra dev
uv run peovim            # always runs from the repo's virtualenv
```

---

## Configuration

The main user config file is `init.py`:

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\peovim\init.py` |
| Linux    | `~/.config/peovim/init.py` |
| macOS    | `~/Library/Application Support/peovim/init.py` |

The file is plain Python. Sub-APIs (`keymap`, `options`, `ui`, `commands`, `events`,
`plugins`, `git`, `lsp`) are injected into the namespace automatically.

Typical pattern:

```python
plugins.load("peovim.plugins.picker")
plugins.load("peovim.plugins.explorer")
plugins.load("peovim.plugins.lsp")

options.set("leader", " ")
keymap.nmap("<leader>w", ":w<CR>", desc="Save file")
```

Built-in plugins are not loaded automatically — your `init.py` opts in to each one.
Load plugins before adding mappings or commands that depend on their `<Plug>` names.

Project-local `.peovim/init.py` is supported. On first encounter the editor prompts
for a trust decision and persists it in shada. Review the file before trusting it.

---

## Files, buffers, windows, and tabs

### Files and buffers

A file on disk is loaded into a `Document`. Multiple windows can show the same document.

Common ex commands:

| Command | Action |
|---------|--------|
| `:e <path>` | Open a file |
| `:e` | Reload the current file (if clean) |
| `:e!` | Reload and discard unsaved changes |
| `:w` | Save |
| `:w <path>` | Save to a new path |
| `:w!` | Force save if the file changed on disk after you loaded it |
| `:q` | Close window |
| `:q!` | Close window, discarding unsaved changes |
| `:wq` / `:x` | Save and close |
| `:bd` | Close buffer (return to alternate) |

If a file changes on disk after you opened it, `:w` stops and warns before overwriting.
Use `:e!` to reload from disk or `:w!` to force the write.

`<C-^>` (or `<C-6>`) — toggle the alternate (last-visited) file.

### Splits

| Command | Action |
|---------|--------|
| `:split` / `:sp` | Horizontal split |
| `:vsplit` / `:vs` | Vertical split |
| `:close` / `:cl` | Close current split |
| `:only` | Close all other splits |

Window navigation and resize (`<C-w>` prefix):

| Key | Action |
|-----|--------|
| `<C-w>h/j/k/l` | Move focus left / down / up / right |
| `<C-w>w` | Cycle to next window |
| `<C-w>s` | Horizontal split (keyboard) |
| `<C-w>v` | Vertical split (keyboard) |
| `<C-w>q` | Close window |
| `<C-w>o` | Close all other windows (`:only`) |
| `<C-w>=` | Equalize all window sizes |
| `<C-w>>` / `<C-w><` | Grow / shrink width |
| `<C-w>+` / `<C-w>-` | Grow / shrink height |

### Tabs

| Key / Command | Action |
|---------------|--------|
| `gt` | Next tab |
| `gT` | Previous tab |
| `:tabnew` | Open a new tab |
| `:tabn` / `:tabnext` | Next tab |
| `:tabp` / `:tabprev` | Previous tab |
| `:tabclose` | Close current tab |

---

## Modal editing

`peovim` uses normal, insert, visual, visual-block, operator-pending, and replace-style flows.

Highlights:
- standard motions: `h`, `j`, `k`, `l`, `w`, `b`, `e`, `0`, `^`, `$`, `gg`, `G`, `f`, `t`, `%`
- operators: `d`, `c`, `y`, `>`, `<`, `g~`, `gu`, `gU`
- text objects: word, quote, bracket, paragraph, sentence families
- visual block editing supports block insert/append, block replace, block paste, and reselect with `gv`
- dot-repeat covers many recent higher-level edits, including block edits and cursor-relative replace/delete cases
- `gf` — open the file path under the cursor

See [keys.md](keys.md) for the complete keybinding reference.

---

## Search and navigation

### Search

| Key | Action |
|-----|--------|
| `/` | Forward search |
| `?` | Backward search |
| `n` / `N` | Next / previous match |
| `*` / `#` | Search word under cursor forward / backward |
| `<Esc>` (normal mode) | Clear search highlighting |

### Jump list

| Key | Action |
|-----|--------|
| `<C-o>` | Jump back (cross-file) |
| `<C-i>` | Jump forward |

---

## Pickers and sidebars

Common picker mappings depend on your `init.py` config. Typical built-ins (via
`peovim.plugins.picker`):
- file picker
- recent files
- open buffers
- live grep
- diagnostics
- commands

The sidebar framework is used for explorer, diagnostics, outline, references, git
status, and marker groups. See [plugins.md](plugins.md) for plugin-specific keys.

---

## Git and diff workflows

The editor supports a lightweight git workflow:
- gutter signs for changed lines (via `peovim.plugins.gitsigns`)
- git status sidebar (`<leader>gs`)
- branch/sync/log helpers
- diff launch for changed files
- stage / unstage / discard files from the panel (`a` / `u` / `x`)

To log git commands as notifications while they run, add to `init.py`:

```python
api.git.verbose = True
```

This echoes user-triggered operations (add, commit, push, pull, etc.) as info
notifications. Background polling commands are filtered out.

The side-by-side diff workflow supports:
- selecting two files
- opening a dedicated diff layout
- jumping between diff blocks
- merging the active block left→right or right→left
- refreshing the diff after saves

---

## LSP features

When a server is configured and available, the editor can provide:
- hover docs
- definition / implementation / type definition
- references
- rename
- document symbols / workspace symbols
- code actions
- diagnostics
- signature help
- completion popup
- optional inlay hints
- optional document highlights

Load the LSP plugin in your `init.py`:

```python
plugins.load("peovim.plugins.lsp")
```

See [keys.md](keys.md) for default LSP keybindings.

---

## Sessions, recent files, and persistence

The editor persists across runs:
- recent files
- command history
- search history
- global marks
- numbered registers
- jump list
- last cursor positions per file
- named sessions

Practical guidance:
- this is good enough for normal daily use
- if two editor processes are both updating the same shared state, later writes can
  overwrite earlier ones
- session save/restore is convenient, but not designed as a conflict-aware
  multi-process store

---

## Reliability expectations

Today the editor is usable on real files, especially for source editing, navigation,
search, and split-based workflows.

Still treat these areas with more caution than in a decades-mature editor:
- project-local config trust UX is functional but minimal
- shared persisted state across many concurrent instances
- multi-instance merge/locking for shared state such as sessions and history

---

## Recommended daily-use posture

- Use version control normally
- Keep autosave/session save as convenience, not as the only source of truth
- Prefer one editor instance per project when using project-local helper data
- Review `.peovim/init.py` before trusting it; the decision is persisted per project root
