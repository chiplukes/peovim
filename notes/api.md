# Plugin API

Derived from survey of ~120 plugins across Vim, Neovim, and VS Code ecosystems.
See `plugins.md` for individual plugin analysis and `plugin_api_survey.md` for
the raw survey data.

---

## Design Principles

**1. Namespace isolation is mandatory.**
Every plugin declares a namespace string. All decorations, highlights, signs, and
event handlers are scoped to that namespace. `buffer.clear_namespace(ns)` removes
everything atomically — no stale state, no flicker on re-render.

**2. Async is the default.**
All I/O, subprocess, LSP, and long-running operations are async. The event loop
never blocks. Plugins that block for >1ms cause perceptible lag.

**3. Completion, diagnostics, and the picker are pipelines.**
They accept pluggable sources. Plugins register sources; the editor runs the pipeline.
No plugin owns the whole system.

**4. `apply_edits` is the fundamental mutation primitive.**
All multi-point edits (formatters, code actions, AI rewrites, refactors) use one
atomic batch operation that integrates with undo as a single step.

**5. Virtual buffers enable "data as buffer."**
Plugin-backed buffers (filesystem editor, DB results, HTTP output) use the same
buffer/window model as real files.

**6. UI intercept hooks exist for every built-in UI element.**
The command line, message area, completion popup, hover popup, and input/select
dialogs can all be replaced by plugins. Nothing is hardwired.

---

## Design Decisions

### 1. Project-local config — Trust-prompt model

`<project_root>/.peovim/init.py` is supported. First encounter prompts "Trust this
project's config? [y/N]". Decision persisted in shada. Untrusted configs silently
ignored. Same model as VS Code workspace trust. First persisted version is implemented in `peovim/config/project.py`; richer in-editor trust UX is follow-up work.

Rationale: Per-project LSP server settings and formatter config are a genuine use
case. Arbitrary code from `git clone` without a trust gate is a security risk.

### 2. `diff.*` and `repl.*` — Built-in plugins, not core namespaces

`peovim.plugins.diff` and `peovim.plugins.repl` implement these features using existing
buffer/window/terminal primitives. They are not core API namespaces. This means:
- They can be replaced or improved by community plugins
- Core API is not permanently encumbered

The `repl.*` API block and `diff.*` API block are the **interface those plugins expose**
via `api.get_plugin("repl")` / `api.get_plugin("diff")` — not core API.

### 3. `git.*` — Keep shared utilities, remove single-user methods

**In `git.*` core namespace:** `git.root()`, `git.get_hunks()`, `git.status()`, `git.branch()`, `git.remote_url()`
— used by 2+ built-in plugins.

**Not in `git.*` core:** `git.blame_line()`
— used only by `peovim.plugins.gitblame`; that plugin manages its own subprocess.

### 4. Re-entrancy — Enforced via ReentrancyError

Event handlers that receive mutation events (`buffer_changed`, `cursor_moved`, etc.)
must not synchronously call `buffer.insert/delete/apply_edits/set_lines()`.
They must use `editor.defer(fn, 0)` to schedule changes after the current dispatch.

Direct plugin keymap callbacks remain allowed to mutate synchronously for now.
The `ActionDispatcher` sets `_dispatching: bool = True` during event handler
invocation. Any synchronous buffer mutation call while this flag is set raises
`ReentrancyError("Plugin 'X' mutated buffer inside buffer_changed handler — use editor.defer()")`.
This is a hard error in development; production builds log and skip.

### 5. API versioning — Semantic versioning

```python
api.VERSION: tuple[int, int, int] = (0, 1, 0)
# Bumped on every breaking change. 0.x.x = pre-stable (anything may change).
# 1.0.0 declared when the plugin API is complete and tested.

api.requires_version(min_version: str) -> None
# Raises PluginVersionError if api.VERSION < parse(min_version).
# Plugins call this in setup() to guard against running on too-old an editor.
```
Deprecation: `@deprecated(since="X.Y.Z", removed_in="X.Y+2.Z")` decorator on
methods emits a `PluginLogger.warning()` for 2 minor versions, then raises `RemovedError`.

---

## API Status

- **Implemented:** `editor`, `buffer`, `window`, `workspace`, `keymap`, `commands`, `events`, `modal`, `options`, `store`, `ui`, `health`
- **Stub (class exists, no methods):** `registers`
- **Experimental:** `lsp`, `git`, `session`
- **Planned:** `completion`, `diagnostics`, `snippets`, `syntax`, `debug`, `testing`, `quickfix`, `jumplist`

> **Note:** Within implemented namespaces, individual methods marked `# planned` below are designed but not yet exposed on the API object.

`diff` and `repl` are built-in plugin interfaces, not core namespaces. Access via
`api.get_plugin("repl")` / `api.get_plugin("diff")`.

---

## API Namespaces

```
editor.*        — top-level editor state and operations
buffer.*        — buffer content, decorations, lifecycle
window.*        — viewport, cursor, splits
workspace.*     — tab pages, layout
keymap.*        — key binding registration
commands.*      — ex command registration
events.*        — event subscription and emission
modal.*         — modal engine mode and visual-selection state
lsp.*           — LSP client interaction
completion.*    — completion pipeline
diagnostics.*   — unified diagnostic system (LSP + linters + tests)
snippets.*      — snippet engine
git.*           — git integration utilities
debug.*         — DAP client
session.*       — session save/restore
store.*         — persistent plugin key-value storage
ui.*            — UI elements (floats, picker, notify, progress)
syntax.*        — tree-sitter access
options.*       — editor and plugin options
registers.*     — register read/write
testing.*       — test runner adapter API (neotest pattern)
# NOTE: repl.* and diff.* are built-in PLUGIN interfaces, not core namespaces.
# Access via api.get_plugin("repl") / api.get_plugin("diff") if those plugins are loaded.
```

---

## API Reference

### `editor` — Top-level

```python
# Buffer lifecycle
editor.open_buffer(path: str | Path, line: int = 0, col: int = 0) -> None  # opens into active window
editor.open_scratch_buffer(text: str = '', *, filetype: str = '', name: str = '') -> Buffer
editor.close_buffer(buf: Buffer, force: bool = False) -> None  # planned
editor.list_buffers() -> list[Buffer]
editor.active_buffer() -> Buffer
editor.active_window() -> Window

# Working directory
editor.cwd() -> Path
editor.set_cwd(path: Path) -> None

# Workspace roots (for LSP and project detection)
editor.add_workspace_root(path: Path) -> None  # planned
editor.workspace_roots() -> list[Path]         # planned
editor.find_root(markers: list[str] | None = None) -> Path | None  # no from_path param

# File system utilities (uses rg when available, Python walk fallback)
editor.find_files(pattern: str = '**/*', root: Path | None = None) -> list[Path]
editor.grep(pattern: str, root: Path | None = None,
            file_pattern: str = '*') -> list[tuple[Path, int, str]]
# grep returns [(path, line_num, line_text), ...]

# External processes  # planned
editor.spawn(
    cmd: list[str],
    stdin: str | bytes | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    on_exit: Callable[[int], None] | None = None,
) -> JobHandle

# File watching  # planned
editor.watch(path: Path, callback: Callable[[WatchEvent], None],
             recursive: bool = False) -> WatchHandle

# URI scheme registration — enables remote file editing (scp://, ssh://, etc.)  # planned
editor.register_uri_handler(
    scheme: str,
    handler: Callable[[str], Awaitable[Buffer]],
    # handler receives the full URI string and must return a Buffer
    # Typically creates a virtual buffer whose content_provider reads/writes remote
) -> None
# e.g. editor.open_buffer('scp://host/path/file.py') dispatches to 'scp' handler

# Python environment detection  # planned
editor.find_venv(from_path: Path) -> Path | None
# Searches from_path upward for: .venv/, venv/, poetry.lock, uv.lock, Pipfile.lock

# OS-level open (xdg-open / open / start depending on platform)  # planned
editor.open_external(target: str | Path) -> None  # gx, browser preview, file manager

# Filetype hook shorthand
editor.on_filetype(
    ft: str,   # single filetype string only (list[str] planned)
    fn: Callable[[Buffer], None | Awaitable[None]],
) -> EventToken
# Equivalent to events.on("filetype_detected", ...) with pattern matching

# Timers — clean alternative to raw asyncio.call_later() in plugins
editor.defer(fn: Callable, ms: int) -> TimerHandle          # one-shot
editor.set_interval(fn: Callable, ms: int) -> TimerHandle   # repeating
timer_handle.cancel() -> None

# Status line / transient user feedback
editor.set_status(message: str, *, notify: bool = True,
                  level: str = "info", title: str = "",
                  timeout: float = 3.0) -> None

# Window enumeration / activation for window-oriented plugins
editor.list_windows() -> list[Window]
editor.list_tab_windows() -> list[Window]
editor.window_by_id(win_id: int, *, active_tab_only: bool = False) -> Window | None
editor.activate_window(window: Window) -> None
editor.alternate_file() -> tuple[Path | None, tuple[int, int]]
editor.open_alternate_buffer() -> bool
editor.record_jump() -> None
editor.add_window_overlay(window: Window, namespace: str, decoration: Decoration) -> int
editor.clear_window_namespace(window: Window, namespace: str) -> None
editor.set_compare_status(status: dict | None) -> None

# Register / window action helpers
editor.set_register(name: str, text: str, kind: str = "char") -> None
editor.get_register(name: str) -> tuple[str, str]
editor.paste_register(name: str = '"', *, before: bool = False) -> None
editor.split_window(direction: str = "v", path: str | Path | None = None) -> None
editor.close_window() -> None
editor.only_window() -> None
editor.equalize_windows() -> None

# Scratch/recent-file helpers
editor.push_recent_file(path: str | Path) -> None
buffer.set_text(text: str) -> None   # sets full buffer content; see also buffer section

# Mode/buffer lookup helpers
editor.active_mode -> Mode
editor.buffer_by_id(buf_id: int) -> Buffer | None

# Which-key panel helpers (on ui, not editor)
ui.show_which_key(pairs: list[tuple[str, str]], *, title: str = "Which Key") -> None
ui.hide_which_key() -> None

# API versioning
api.VERSION: tuple[int, int, int]      # e.g. (0, 1, 0)
api.requires_version(min: str) -> None # raises PluginVersionError if too old

# Plugin inter-op  # planned
api.get_plugin(name: str) -> Any | None  # access another plugin's exported API
# e.g. api.get_plugin("repl") -> ReplPlugin | None

# Mode
editor.active_mode -> Mode  # NORMAL | INSERT | VISUAL_* | COMMAND | ...

# Highlight groups (theme-compatible)  # planned
editor.set_highlight(name: str, fg: str | None = None, bg: str | None = None,
                     bold: bool = False, italic: bool = False,
                     underline: bool = False, strikethrough: bool = False,
                     link: str | None = None) -> None
editor.get_highlight(name: str) -> HighlightStyle

# Dot-repeat registration
editor.set_dot_repeat(action: Action) -> None  # called by plugins after an operation

# Cross-buffer atomic transaction — all changes form ONE undo step across all buffers.  # planned
with editor.transaction(description: str = ''):
    buf_a.apply_edits(edits_a)
    buf_b.apply_edits(edits_b)
    # All 50 files changed by rename-symbol → single Ctrl-Z reverses all of them

# Run normal-mode key sequence from ex command or plugin code  # planned (use keymap.feed_keys() instead)
editor.normal(keys: str, buffer: Buffer | None = None) -> None

# Plugin logger — structured log, never interrupts TUI
editor.get_logger(name: str) -> PluginLogger
# PluginLogger: .debug(), .info(), .warning(), .error(exc_info=False)
# Output: peovim://log virtual buffer + optional :EdLog command

# Mode/filetype  # planned
editor.detect_filetype(path: Path, first_line: str = '') -> str
editor.add_filetype_rule(
    pattern: str | None = None,      # glob, e.g. '*.v' or '*.sv'
    first_line: str | None = None,   # regex matched against line 1
    ft: str = '',
) -> None

# Persistent native state — marks (A-Z), numbered registers, command/search history,
# jump list. Written to ~/.local/share/peovim/shada (platform-adjusted).
editor.shada_write() -> None
editor.shada_read() -> None
# Called automatically on startup/shutdown; plugins don't normally call this.
```

---

### `buffer` — Buffer Content and Decorations

#### Read operations
```python
buffer.buf_id -> int             # stable numeric ID for this buffer
buffer.path -> Path | None
buffer.name -> str               # filename component of path, or "[Scratch]"
buffer.filetype -> str
buffer.encoding -> str           # e.g. "utf-8"
buffer.line_ending -> str        # "\n", "\r\n", or "\r"
buffer.is_readonly -> bool
buffer.is_listed -> bool         # whether buffer appears in buffer lists
buffer.set_listed(listed: bool) -> None
buffer.version -> int            # monotonic counter; incremented on every mutation
buffer.is_modified() -> bool
buffer.is_valid() -> bool        # False after buffer is closed; check before using stale refs
buffer.line_count() -> int
buffer.get_line(n: int) -> str                          # 0-indexed
buffer.get_lines(start: int = 0, end: int | None = None) -> list[str]
buffer.get_text() -> str         # full buffer text; range variant planned
buffer.get_option(name: str) -> Any
buffer.set_option(name: str, value: Any) -> None
```

#### Write operations — all integrate with undo stack
```python
buffer.insert(line: int, col: int, text: str) -> None
buffer.delete(line_start: int, col_start: int,
              line_end: int, col_end: int) -> None
buffer.replace(line_start, col_start, line_end, col_end,
               new_text: str) -> None

buffer.apply_edits(edits: list[TextEdit]) -> None
# TextEdit = (line_start, col_start, line_end, col_end, new_text)
# Applies edits in reverse line order (LSP convention) — one undo step.

buffer.set_lines(start: int, end: int, lines: list[str]) -> None
# Replaces lines [start, end).

# Begin/end a compound edit (multiple operations = one undo step)
with buffer.batch():
    buffer.insert(...)
    buffer.delete(...)

# Streaming insert — for AI completion / generative text.  # planned
with buffer.begin_stream(line: int, col: int):
    buffer.stream_append(text: str) -> None   # called repeatedly as tokens arrive
```

#### Lifecycle
```python
buffer.save() -> None                        # raises ValueError if no path set
buffer.save_as(path: str | Path) -> None     # saves to new path and updates buffer.path
buffer.reload() -> None                      # reload from disk, discards unsaved changes
buffer.set_filetype(ft: str) -> None         # override detected filetype
```

#### Decorations (namespace-isolated)
```python
# Highlight a character range. Returns an ID that tracks with buffer edits (extmark).
id = buffer.add_highlight(
    ns: str,                       # namespace — FIRST arg
    start_line: int, start_col: int, end_line: int, end_col: int,
    style: str | HighlightStyle,
    priority: int = 0,
) -> int
buffer.remove_highlight(ns: str, dec_id: int) -> None

# Virtual text on a line
id = buffer.add_virtual_text(
    ns: str,            # namespace — FIRST arg
    line: int,
    text: str,
    style: str | HighlightStyle,
    priority: int = 0,
    # planned: placement, col (inline/overlay positioning)
) -> int
buffer.remove_virtual_text(ns: str, dec_id: int) -> None

# Virtual line — blank coloured row(s) inserted after a buffer line (used for diff alignment)
id = buffer.add_virtual_line(
    ns: str,
    after_line: int,   # buffer line anchor; -1 = before line 0
    style: Style,      # background colour for the blank row
    count: int = 1,    # how many identical blank rows to insert
) -> int
buffer.remove_virtual_line(ns: str, id: int) -> None

# Sign in gutter
#
# Sign types must be registered once (typically in plugin setup()):
#   editor.register_sign_type(
#       name: str,           # e.g. 'gitsigns.add', 'lsp.error', 'dap.breakpoint'
#       char: str,           # exactly 1 character (│, ●, ▶, ✖, etc.)
#       style: HighlightStyle,
#   )
#
# Signs are placed per-line. The highest-priority sign on a line wins the
# 1-character sign column slot. Ties broken by ns sort order.
# signcolumn option controls visibility: "yes" | "no" | "auto" (default).
#
id = buffer.add_sign(
    ns: str,                     # namespace — FIRST arg
    line: int,
    sign_type_name: str,         # registered via editor.register_sign_type()
    priority: int = 0,
) -> int
buffer.remove_sign(ns: str, dec_id: int) -> None

# Convenience: place a sign directly without a registered type (ephemeral)
id = buffer.add_sign_raw(
    ns: str,            # namespace — FIRST arg
    line: int,
    char: str,
    style: HighlightStyle,
    priority: int = 0,
) -> int

# Example: gitsigns plugin
editor.register_sign_type('gitsigns.add',    char='│', style=Style(fg=(0,200,0)))
editor.register_sign_type('gitsigns.change', char='│', style=Style(fg=(200,200,0)))
editor.register_sign_type('gitsigns.delete', char='▁', style=Style(fg=(200,0,0)))

# Example: LSP diagnostics
editor.register_sign_type('lsp.error',   char='●', style=Style(fg=(220,50,50)))
editor.register_sign_type('lsp.warning', char='●', style=Style(fg=(220,180,0)))
editor.register_sign_type('lsp.info',    char='●', style=Style(fg=(100,180,220)))
editor.register_sign_type('lsp.hint',    char='●', style=Style(fg=(140,140,140)))

# Example: DAP breakpoints
editor.register_sign_type('dap.breakpoint',          char='●', style=Style(fg=(220,50,50)))
editor.register_sign_type('dap.breakpoint_condition',char='◆', style=Style(fg=(220,130,0)))
editor.register_sign_type('dap.stopped',             char='▶', style=Style(fg=(0,220,0)))

# Inlay hint (between tokens — LSP type hints, parameter names)  # planned
id = buffer.add_inlay_hint(
    ns: str, line: int, col: int,
    text: str, style: str | HighlightStyle,
    side: Literal['before', 'after'] = 'after',
) -> int

# Ghost text (provisional completion at cursor — copilot/blink)
id = buffer.set_ghost_text(
    ns: str,             # namespace — FIRST arg
    line: int, col: int,
    text: str,
    style: str | HighlightStyle | None = None,
    # planned: on_accept, on_dismiss callbacks
) -> int
buffer.clear_ghost_text(ns: str) -> None

# Conceal (hide characters in display, show substitute instead)  # planned
id = buffer.add_conceal(
    ns: str, line: int, col_start: int, col_end: int,
    substitute: str = '',        # '' = completely hidden; single char = shown instead
) -> int

# CodeLens (actionable virtual line above a function/class)  # planned
id = buffer.add_codelens(
    ns: str, line: int,
    text: str,
    on_activate: Callable[[], None],   # called on Enter / click
) -> int

# Read back decoration positions after edits have moved them  # planned
buffer.get_extmarks(
    ns: str,
    line_start: int = 0,
    line_end: int = -1,    # -1 = end of buffer
) -> list[ExtmarkInfo]
# ExtmarkInfo: id, line, col, type ('highlight'|'sign'|'virtual_text'|...)

# Add a pre-built decoration object directly (lower-level than typed helpers above)
id = buffer.add_decoration(ns: str, dec: object) -> int

# Atomic clear of everything in a namespace
buffer.clear_namespace(ns: str) -> None
buffer.list_namespaces() -> list[str]  # planned

# Sign type registration (global)
editor.register_sign_type(
    name: str,
    symbol: str,     # 1-2 char glyph (or Nerd Font icon)
    fg: str | None = None,
    bg: str | None = None,
    hl: str | None = None,       # link to highlight group
) -> None
```

#### Syntax access
```python
buffer.get_syntax_tree() -> SyntaxNode | None     # root of tree-sitter parse tree
buffer.query_syntax(query: str) -> list[tuple[str, SyntaxNode]]
  # query is an S-expression (.scm) string; returns (capture_name, node) pairs
buffer.node_at_cursor() -> SyntaxNode | None
buffer.scope_at_cursor() -> SyntaxNode | None     # enclosing scope node
buffer.fold_ranges() -> list[FoldRange]           # from tree or LSP fold provider
buffer.indent_at(line: int) -> int                # computed indent level
```

#### Virtual buffer provider (oil.nvim pattern)
```python
editor.create_virtual_buffer(
    name: str,
    content_provider: Callable[[], str | Awaitable[str]],
    save_handler: Callable[[str], None | Awaitable[None]] | None = None,
    filetype: str = '',
    readonly: bool = False,
) -> Buffer
```

---

### `window` — Viewport and Cursor

```python
window.win_id -> int                   # stable numeric ID for this window
window.buffer() -> Buffer              # method, not property
window.cursor -> tuple[int, int]       # (line, col), 0-indexed
window.set_cursor(line: int, col: int) -> None   # no add_to_jumplist param
window.set_scroll_line(line: int) -> None
window.scroll_to_cursor() -> None      # centers cursor; scroll_to(line) planned
window.scroll_offset -> tuple[int, int]   # (scroll_line, scroll_col) of visible top-left
window.visible_range() -> tuple[int, int]   # (first_line, last_line) inclusive
window.get_width() -> int
window.get_height() -> int
window.is_valid() -> bool              # method, not property
window.is_focused() -> bool
window.get_option(name: str) -> Any
window.set_option(name: str, value: Any) -> None

# Visual selection
window.get_visual_selection() -> tuple[str, tuple[int,int], tuple[int,int]] | None
# Returns (mode, start, end) where mode is 'char'|'line'|'block'.
# Returns the active selection if in Visual mode, else the last selection.
# Returns None if no selection exists yet.

# Layout
window.split(direction: Literal['h', 'v'] = 'v', buffer: Buffer | None = None) -> Window
# 'v' = vertical split (side by side), 'h' = horizontal split (stacked)
window.close() -> None    # raises ValueError if last window in tab
window.focus() -> None    # makes this window active

# planned:
window.set_visual_selection(start: tuple[int,int], end: tuple[int,int]) -> None
window.get_word_at_cursor() -> str
window.set_decoration_provider(
    ns: str,
    on_win: Callable[[Window, int, int], None],
    on_line: Callable[[Window, int], None] | None = None,
) -> None
```

---

### `workspace` — Layout and Tabs

```python
workspace.active_tab -> TabPage                        # the active TabPage object
workspace.list_tabs() -> list[TabPage]                 # all tab pages
workspace.new_tab(buffer: Buffer | None = None) -> Window  # opens new tab; returns WindowAPI
workspace.close_tab(tab: TabPage) -> None              # raises ValueError if last tab
workspace.list_windows() -> list[Window]               # windows in active tab
workspace.find_window(buffer: Buffer) -> Window | None # first window showing buffer, or None
```

---

### `keymap` — Key Binding Registration

```python
keymap.leader -> str          # set in init.py
keymap.local_leader -> str

# Standard mappings (all noremap by default)
keymap.nmap(keys: str, action: str | Callable, desc: str = '') -> None
keymap.imap(keys: str, action: str | Callable, desc: str = '') -> None
keymap.vmap(keys: str, action: str | Callable, desc: str = '') -> None
# planned: keymap.xmap(), keymap.omap(), keymap.tmap(), keymap.cmap()

# Unmap (normal mode only; mode param planned)
keymap.unmap(keys: str) -> None
keymap.vunmap(keys: str) -> None
keymap.iunmap(keys: str) -> None

# <Plug> mappings — stable internal names for plugin actions
keymap.define_plug(name: str, action: Callable, desc: str = '') -> None
keymap.define_vplug(name: str, action: Callable, desc: str = '') -> None   # visual-mode plug
keymap.invoke_plug(name: str) -> bool

# <expr> mapping — RHS is a function returning a key string to replay  # planned
keymap.nmap_expr(keys: str, fn: Callable[[], str]) -> None

# Fallthrough mapping — handler returns False to pass to next handler  # planned
keymap.imap_ft(keys: str, fn: Callable[[], bool | None]) -> None

# Register a new operator (like 'd', 'y', 'c' but plugin-defined)  # planned
keymap.register_operator(
    key: str,
    fn: Callable[[Buffer, Range, str], None],   # (buffer, range, register) -> None
    desc: str = '',
) -> None

# Register a new motion (usable anywhere a motion is accepted)  # planned
keymap.register_motion(
    key: str | list[str],
    fn: Callable[[Window, int], tuple[int, int]],  # (window, count) -> (line, col)
    desc: str = '',
    inclusive: bool = False,
) -> None

# Register a new text object (usable after operators)  # planned
keymap.register_text_object(
    inner_key: str,    # key for 'i' variant (e.g. 'f' → 'if')
    outer_key: str,    # key for 'a' variant (e.g. 'f' → 'af')
    fn: Callable[[Window, Literal['inner', 'outer'], int], Range],
    desc: str = '',
) -> None

# Query bindings (for which-key)
keymap.get_bindings(mode: str | None = None) -> list[BindingInfo]
# BindingInfo: keys, action_desc, source ('builtin' | 'user' | plugin_name)
keymap.find_keys_for_plug(name: str, mode: str = 'normal') -> list[str]
keymap.get_group_name(prefix: str) -> str

# Declare a key prefix as a named group (for which-key display)
keymap.ngroup(keys: str, name: str) -> None
# e.g. keymap.ngroup('<leader>f', 'Find')

# Feed key sequence to the modal engine (used by snippets, tests, plugin modes)
keymap.feed_keys(keys: str, remap: bool = True) -> None
# keys uses Vim notation: '<Esc>', '<CR>', '<C-x>', etc.
```

---

### `commands` — Ex Command Registration

```python
commands.register(
    name: str,
    handler: Callable,
    *,
    min_abbrev: int = 0,   # minimum prefix length for abbreviation matching
    desc: str = '',
    # planned: nargs, range_allowed, complete
) -> None

commands.unregister(name: str) -> None
commands.execute(command_text: str, ctx: Any = None) -> Any  # parse and run an ex command
commands.list_commands() -> list[str]
```

---

### `events` — Event System

```python
# Subscribe (returns a token for unsubscribing)
token = events.on(event: str, handler: Callable,
                  pattern: str | None = None) -> EventToken
# pattern is a glob matched against buffer path for buffer events

events.off(token: EventToken) -> None

# Decorator form
@events.on("buffer_saved")
def handle_save(buffer): ...

@events.on("buffer_opened", pattern="*.py")
def setup_python(buffer): ...

# Emit user-defined events (plugin-to-plugin communication)
events.emit(event: str, **kwargs) -> None

# One-shot subscription
events.once(event: str, handler: Callable) -> None
```

#### Standard events

| Event | Payload | Status |
|---|---|---|
| `buffer_opened` | `buffer` | fired |
| `buffer_changed` | `buffer, change: TextChange` | fired |
| `buffer_saved` | `buffer` | fired |
| `buffer_pre_save` | `buffer` → handler can raise `CancelSave` | fired |
| `filetype_detected` | `buffer, filetype` | fired |
| `cursor_moved` | `window` | fired |
| `insert_entered` | `window` | fired |
| `insert_left` | `window` | fired |
| `mode_changed` | `from_mode, to_mode` | fired |
| `option_changed` | `name, old_value, new_value, scope` | fired |
| `diagnostics_updated` | `buffer` | fired (note plural) |
| `yank_done` | `register_name, text, type` | fired |
| `editor_ready` | _(no payload)_ | fired |
| `editor_shutdown` | _(no payload)_ | fired |
| `buffer_closed` | `buffer` | planned |
| `buffer_entered` | `buffer, window` | planned |
| `buffer_left` | `buffer, window` | planned |
| `cursor_moved_insert` | `window` | planned |
| `window_entered` | `window` | planned |
| `window_resized` | `window` | planned |
| `window_closed` | `window` | planned |
| `tab_entered` | `tab` | planned |
| `colorscheme_changed` | `name` | planned |
| `lsp_attached` | `buffer, server_name` | planned |
| `lsp_detached` | `buffer, server_name` | planned |

Also: `events.handler_count(event: str) -> int` — number of registered handlers.

---

### `modal` — Modal Engine State

Read or modify the current editor mode and visual selection from plugins.

```python
from peovim.modal.engine import Mode

# Read current mode
current_mode = modal.mode()   # returns Mode enum value

# Visual selection anchor (preserved across mode transitions)
anchor = modal.visual_anchor()  # returns (line, col) tuple

# Set mode (use Mode enum values)
modal.set_mode(Mode.VISUAL_CHAR)

# Set visual anchor
modal.set_visual_anchor(line, col)
```

Typical use: plugins that exit and re-enter visual mode (e.g. flash jump).

---

### `lsp` — LSP Client

```python
lsp.register_server(
    filetype: str,                   # single filetype string
    cmd: list[str],
    root_markers: list[str] | None = None,
    # planned: name, filetypes (list), settings, init_options, capabilities_override, on_attach
) -> None

# Server info
lsp.registered_servers() -> list[dict]
lsp.running_servers() -> list[dict]
lsp.current_buffer_status() -> dict
lsp.info() -> None                     # show LSP status in a float
lsp.restart(filetype: str) -> None

# Buffer attachment
lsp.attach_buffer(buf_id: int = 0) -> Any
lsp.notify_buffer_changed(buf_id: int = 0) -> Any
lsp.notify_buffer_saved(buf_id: int = 0) -> Any
lsp.attach_open_buffers() -> None
lsp.flush_pending_changes() -> None

# Standard LSP actions (dispatch to server for active buffer)
lsp.hover() -> None
lsp.definition() -> None
lsp.implementation() -> None
lsp.type_definition() -> None
lsp.references() -> None
lsp.references_search(cb: Callable) -> None
lsp.code_actions() -> None
lsp.rename() -> None
lsp.signature_help() -> None
lsp.dismiss_signature_help() -> None
lsp.document_symbols() -> None
lsp.document_symbol_tree(cb: Callable) -> None
lsp.workspace_symbols() -> None
lsp.workspace_symbol_search(query: str, cb: Callable) -> None
lsp.apply_workspace_edit(edit: dict) -> None

# Inlay hints
lsp.toggle_inlay_hints() -> None
lsp.refresh_inlay_hints() -> None
lsp.clear_inlay_hints() -> None

# Document highlights
lsp.refresh_document_highlight() -> None
lsp.clear_document_highlight() -> None

# Diagnostics navigation
lsp.goto_next_diag() -> None
lsp.goto_prev_diag() -> None
lsp.diag_detail() -> None

# Completion
lsp.trigger_completion() -> None

# Event hooks
lsp.on_notification(method: str, callback: Callable) -> None
lsp.on_progress(callback: Callable) -> None

# Ad-hoc requests  # planned
lsp.request(buffer: Buffer, method: str, params: dict) -> Awaitable[Any]
lsp.custom_request(method: str, params: dict, cb: Callable) -> None
lsp.custom_request_to(method: str, params: dict, cb: Callable, *, cmd_contains: str) -> None
lsp.override_handler(method: str, handler: Callable | None) -> None
lsp.set_token_highlight(token_type: str, highlight_group: str) -> None

# Diagnostics remap (internal — used after buffer edits)
lsp.remap_buffer_diagnostics(*, buf_id: int, start_line: int, start_col: int,
                              end_line: int, end_col: int, new_text: str) -> None
```

---

### `diagnostics` — Unified Diagnostic System

LSP, linters, test runners, and spell checkers all use this one API.

```python
@dataclass
class Diagnostic:
    buffer: Buffer
    line: int
    col_start: int
    col_end: int
    severity: Literal['error', 'warn', 'info', 'hint']
    message: str
    source: str       # 'pylsp', 'ruff', 'neotest', 'spell', etc.
    code: str | None = None
    tags: list[Literal['deprecated', 'unnecessary']] = field(default_factory=list)
    data: Any = None  # opaque, for 'resolve' or quick-fix data

# Read
diagnostics.get(buffer: Buffer,
                severity: str | None = None,
                source: str | None = None) -> list[Diagnostic]
diagnostics.get_line(buffer: Buffer, line: int) -> list[Diagnostic]
diagnostics.count(buffer: Buffer) -> dict[str, int]  # severity → count

# Write (plugins inject diagnostics — linters, test runners, spell checkers)
diagnostics.set(buffer: Buffer, items: list[Diagnostic], source: str) -> None
diagnostics.clear(buffer: Buffer, source: str) -> None
diagnostics.clear_all(source: str) -> None
```

---

### `completion` — Completion Pipeline

```python
@dataclass
class CompletionContext:
    buffer: Buffer
    line: int
    col: int
    prefix: str          # word before cursor
    trigger_char: str    # character that triggered completion ('' if manual)
    trigger_kind: Literal['auto', 'manual', 'trigger_char']

@dataclass
class CompletionItem:
    label: str
    kind: str            # 'function' | 'class' | 'variable' | 'keyword' | 'snippet' | 'file' | ...
    detail: str = ''
    documentation: str = ''       # markdown
    insert_text: str | None = None  # defaults to label
    insert_format: Literal['text', 'snippet'] = 'text'
    filter_text: str | None = None
    sort_text: str | None = None
    additional_edits: list[TextEdit] = field(default_factory=list)  # e.g. auto-import
    commit_chars: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # 'deprecated'
    data: Any = None    # opaque, passed to resolve()

completion.register_source(
    name: str,
    complete: Callable[[CompletionContext], Awaitable[list[CompletionItem]]],
    resolve: Callable[[CompletionItem], Awaitable[CompletionItem]] | None = None,
    trigger_characters: list[str] = [],
    priority: int = 100,   # higher = ranked first
) -> None

completion.register_sorter(
    name: str,
    sort: Callable[[list[CompletionItem], CompletionContext], list[CompletionItem]],
) -> None
```

---

### `snippets` — Snippet Engine

```python
snippets.expand(text: str, buffer: Buffer | None = None) -> None
# Expands VSCode snippet syntax at current cursor position.
# Tabstops become tracked extmarks; Tab/S-Tab navigates between them.

snippets.is_active() -> bool
snippets.next_tabstop() -> None
snippets.prev_tabstop() -> None
snippets.exit() -> None

# Register snippets for a filetype
snippets.register(
    trigger: str,
    body: str | Callable[[SnippetContext], str],
    filetypes: list[str] = [],   # [] = all filetypes
    desc: str = '',
) -> None

# Load snippet files
snippets.load_vscode_snippets(path: Path) -> None    # .json VSCode format
snippets.load_ultisnips(path: Path) -> None          # .snippets format
```

---

### `git` — Git Utilities

All git operations are **synchronous** (they run `git` as a subprocess, blocking briefly).

```python
# Core (used by 2+ built-in plugins)
git.root(path: Path | None = None) -> Path | None
git.status(path: Path | None = None) -> list[tuple[str, str]]
  # returns [(status_code, filepath), ...] e.g. [('M', 'src/foo.py'), ('??', 'new.py')]
git.get_hunks(buf_path: Path | None = None) -> list[dict]
  # each dict: old_start, old_lines, new_start, new_lines, diff_text
git.branch(path: Path | None = None) -> str
git.remote_url(path: Path | None = None, *, remote: str = 'origin') -> str | None

# Extended git operations
git.branch_info(path: Path | None = None) -> GitBranchInfo
git.status_entries(path: Path | None = None) -> list[GitStatusEntry]
git.repo_state(path: Path | None = None) -> GitRepoState | None
git.list_branches(path: Path | None = None, *, include_remote: bool = False) -> list[GitBranchInfo]
git.remotes(path: Path | None = None) -> list[GitRemote]
git.log_entries(path: Path | None = None, *, limit: int = 30, ref: str | None = None) -> list[GitLogEntry]
git.show_file_text(repo_path: Path, *, path: Path | None = None, ref: str = 'HEAD') -> str | None

# Mutation operations
git.create_branch(name: str, *, path: Path | None = None, start_point: str | None = None) -> None
git.checkout(ref: str, *, path: Path | None = None) -> None
git.merge(ref: str, *, path: Path | None = None) -> None
git.commit(message: str, *, path: Path | None = None) -> None
git.stage_file(file_path: Path, *, path: Path | None = None) -> None
git.unstage_file(file_path: Path, *, path: Path | None = None) -> None
git.discard_file(file_path: Path, *, path: Path | None = None) -> None
git.fetch(*, path: Path | None = None, remote: str | None = None) -> None
git.pull(*, path: Path | None = None, remote: str | None = None, branch: str | None = None) -> None
git.push(*, path: Path | None = None, remote: str | None = None,
         branch: str | None = None, set_upstream: bool = False) -> None

git.verbose: bool  # set True to echo operations as notifications

# Note: git.blame_line() is not in core git.*; use peovim.plugins.gitblame instead.
```

---

### `debug` — Debug Adapter Protocol

```python
debug.register_adapter(type_name: str, config: dict) -> None
debug.start_session(config: dict) -> Awaitable[None]
debug.stop_session() -> Awaitable[None]
debug.is_active() -> bool

debug.toggle_breakpoint(buffer: Buffer, line: int) -> None
debug.list_breakpoints() -> list[Breakpoint]
debug.clear_all_breakpoints() -> None

debug.continue_() -> Awaitable[None]
debug.step_over() -> Awaitable[None]
debug.step_into() -> Awaitable[None]
debug.step_out() -> Awaitable[None]
debug.pause() -> Awaitable[None]

debug.get_stack_frames() -> Awaitable[list[StackFrame]]
debug.get_variables(scope_ref: int) -> Awaitable[list[Variable]]
debug.evaluate(expression: str) -> Awaitable[str]
```

---

### `session` — Save/Restore

```python
# Plugin state hooks — registered at plugin setup time
@session.register("my_plugin")
def save_state() -> dict:
    return {"marks": [...], "history": [...]}

def restore_state(data: dict) -> None:
    ...

# Programmatic session management
session.save(path: Path | None = None) -> None   # None = auto path
session.restore(path: Path | None = None) -> None
session.has_session(path: Path | None = None) -> bool
```

---

### `store` — Persistent Plugin Storage

Plugins use this for data that must survive restarts (harpoon marks, frecency,
DAP breakpoints, telescope history, neoclip yank history).

```python
store = api.store.get_store("my_plugin")   # isolated namespace per plugin; NOT editor.get_store()

store.set(key: str, value: Any) -> None     # value must be JSON-serializable
store.get(key: str, default: Any = None) -> Any
store.delete(key: str) -> None
store.clear() -> None
store.keys() -> list[str]
```

Data is stored as JSON in `~/.local/share/peovim/stores/<plugin_name>.json` on Linux,
`%LOCALAPPDATA%\peovim\stores\<plugin_name>.json` on Windows.

---

### `ui` — UI Elements

#### Floating windows
```python
ui.open_float(
    content: str | list[str],      # string or list of lines
    anchor: FloatAnchor,           # cursor-relative, absolute, or window-relative
    width: int | None = None,
    height: int | None = None,
    border: bool = True,           # True/False; styled border variant planned
    title: str = '',
    focusable: bool = True,
    z_order: int = 0,
    on_close: Callable[[], None] | None = None,
    # planned: footer, filetype, Buffer content
) -> FloatHandle

float_handle.close() -> None
float_handle.set_content(lines: list[str]) -> None
float_handle.is_open() -> bool
```

#### Picker
```python
ui.open_picker(
    title: str,
    source: list[PickerItem] | AsyncGenerator[PickerItem],
    on_confirm: Callable[[list[PickerItem]], None],
    on_close: Callable[[], None] | None = None,
    multi_select: bool = False,
    preview: Callable[[PickerItem], str | Buffer | None] | None = None,
    keymap: dict[str, Callable] | None = None,  # extra keys within picker
    # planned: initial_query param
) -> None

@dataclass
class PickerItem:
    label: str
    detail: str = ''
    icon: str = ''
    highlights: list[tuple[int, int, str]] = field(default_factory=list)
    value: Any = None
    sort_key: str | None = None
```

#### Notifications
```python
ui.notify(
    message: str,
    level: Literal['debug', 'info', 'warn', 'error'] = 'info',
    title: str = '',
    timeout: float = 3.0,    # seconds; 0 = persistent
) -> NotificationHandle

# Progress notifications (for LSP indexing, long operations)  # planned
handle = ui.show_progress(id: str, title: str, message: str = '',
                          pct: int | None = None) -> ProgressHandle
handle.update(message: str, pct: int | None = None) -> None
handle.close() -> None

# Replace notify implementation (for noice-style plugins)  # planned
ui.set_notify_handler(handler: Callable[[str, str, str, float], None]) -> None
```

#### Input / select (replaceable)  # planned
```python
result = await ui.input(prompt: str, default: str = '',
                        completion: Callable[[str], list[str]] | None = None) -> str | None

choice = await ui.select(items: list[str], prompt: str = '') -> str | None

answer = await ui.confirm(
    message: str,
    choices: list[str] = ['Yes', 'No', 'Cancel'],
    default: int = 0,
) -> int | None      # index into choices, None if dismissed

key = await ui.input_char(prompt: str = '') -> str
# Returns key in Vim notation: 'a', '<Esc>', '<C-c>', '<CR>', etc.

ui.set_input_handler(handler: Callable) -> None
ui.set_select_handler(handler: Callable) -> None
```

#### Status bar component registration  # planned
```python
ui.register_statusline_component(
    name: str,
    fn: Callable[[Window], list[StyledText]],
    position: Literal['left', 'center', 'right'] = 'right',
    priority: int = 0,
    min_width: int = 0,
) -> None

ui.register_winbar_component(
    name: str,
    fn: Callable[[Window], list[StyledText]],
    position: Literal['left', 'center', 'right'] = 'left',
) -> None

ui.register_tabline_provider(
    fn: Callable[[list[TabPage]], list[StyledText]],
) -> None
```

#### UI intercept hooks (for noice-style plugins)  # planned
```python
ui.set_cmdline_handler(handler: Callable | None) -> None
ui.set_message_handler(handler: Callable | None) -> None
ui.set_completion_ui_handler(handler: Callable | None) -> None
ui.set_hover_ui_handler(handler: Callable | None) -> None
```

#### Terminal buffer
```python
ui.open_terminal(
    name: str,                     # named terminal (required; identifies the session)
    cmd: list[str] | None = None,  # command to run; None = default shell
    rows: int = 24,
    cols: int = 80,
    # planned: cwd, env, direction, on_exit params
) -> TerminalBuffer

ui.get_terminal(name: str) -> TerminalBuffer | None   # retrieve named terminal
ui.toggle_terminal(name: str) -> None                 # show/hide by name

terminal.send(text: str) -> None       # send text to process stdin
terminal.send_line(text: str) -> None  # send text + newline (execute)
terminal.is_running() -> bool
terminal.kill() -> None
terminal.name -> str | None
terminal.focus() -> None
```

Named terminals survive buffer switches. The `name` field lets plugins manage
"the Python REPL", "the test runner", "the SSH session" as stable identities.

#### REPL integration

```python
# Send text to a running terminal (REPL workflow)
repl.set_default(name: str) -> None              # which terminal is "the REPL"
repl.get_default() -> TerminalBuffer | None
repl.send_line(buffer: Buffer | None = None) -> None       # current line
repl.send_selection(buffer: Buffer | None = None) -> None  # visual selection
repl.send_block(buffer: Buffer | None = None) -> None      # current code block (cell/func)
repl.send_file(buffer: Buffer | None = None) -> None       # whole file

# Cell markers — cells delimited by # %% (Jupyter-style or # --- etc.)
repl.next_cell() -> None
repl.prev_cell() -> None
repl.run_cell(buffer: Buffer | None = None) -> None    # send_block for current cell
```

The `repl.*` API is thin orchestration on top of `terminal.send_line()` — no
separate process, no kernel protocol. Works with any REPL (Python, IPython,
Node, ghci, etc.) because it just sends text to stdin.

#### Tree view (`ui.open_tree()`)

First-class tree/graph navigation widget. Used for symbol outlines, call trees,
Verilog module hierarchies, import graphs, or any directed graph navigable as a tree.

```python
@dataclass
class TreeNode:
    id: str                              # unique within the tree
    label: str                           # primary display text
    detail: str = ''                     # dim secondary text (type, file, etc.)
    icon: str = ''                       # single char / Nerd Font glyph
    children: list['TreeNode'] | None = None  # None = unknown (lazy); [] = known leaf
    data: Any = None                     # opaque plugin payload
    is_cycle: bool = False               # render with cycle indicator
    expanded: bool = False               # initial expansion state
    style: str | None = None             # highlight group override

class TreeViewHandle:
    def close(self) -> None: ...
    def refresh(self, root: TreeNode | None = None) -> None: ...
    def set_cursor(self, node_id: str) -> None: ...
    def get_selected(self) -> TreeNode | None: ...
    def expand(self, node_id: str) -> None: ...
    def collapse(self, node_id: str) -> None: ...

ui.open_tree(
    nodes: TreeNode | list[TreeNode],
    *,
    title: str,
    width: int = 40,
    on_select: Callable[[TreeNode], None],
    on_close: Callable[[], None] | None = None,
    on_key: dict[str, Callable[[TreeNode], None]] | None = None,
    # planned: on_expand (lazy), on_direction_change, direction, height,
    #          follow_cursor, follow_fn, keymaps
) -> TreeViewHandle
```

Default keybindings inside the tree view:

| Key | Action |
|---|---|
| `j` / `k` | Move cursor down / up |
| `l` / `Enter` | Expand node or jump to definition |
| `h` | Collapse node or go to parent |
| `o` | Jump to source in editor (peek — keep tree focused) |
| `O` | Open source in editor and close tree |
| `i` | Toggle incoming direction (e.g. callers) |
| `<Tab>` | Toggle outgoing direction (e.g. callees) |
| `r` | Refresh tree |
| `q` / `Esc` | Close tree |
| `?` | Show help |

#### Dockable panels (sidebar and bottom)

Both the sidebar and bottom panel are instances of `PanelHost` and support the same content protocol: `render(grid: CellGrid) -> None`, `feed_key(key: str) -> bool`.  Optional attributes (`title`, `width`, `on_show`, `on_hide`, `on_focus`, `on_blur`) are queried with `getattr` defaults.

**Unified API** (host-agnostic):

```python
ui.register_panel(name, content, *, host='sidebar')  # 'sidebar' | 'bottom'
ui.move_panel(name, to)     # move between 'sidebar' and 'bottom'; returns bool
ui.show_panel(name, *, focus=True)  # show by name, regardless of host
```

**Sidebar-specific API**:

```python
ui.register_sidebar_panel(name, panel) -> panel
ui.show_sidebar_panel(name, panel, *, focus=True) -> panel
ui.toggle_sidebar_panel(name, panel=None, *, focus=True) -> bool
ui.list_sidebar_panels() -> list[str]
ui.next_sidebar_panel() / ui.prev_sidebar_panel()
ui.show_tree_sidebar(name, nodes, *, title, width, on_select, on_key, focus)
ui.is_sidebar_visible(name=None) -> bool
ui.hide_sidebar() / ui.focus_sidebar() / ui.blur_sidebar()
ui.set_sidebar_style(*, background, header_active_fg, header_active_bg,
                     header_inactive_fg, header_inactive_bg)
ui.sidebar_nmap(key, plug_name)  # bind a sidebar-internal key to a <Plug>
```

**Bottom panel-specific API**:

```python
ui.register_bottom_tab(name, tab)
ui.show_bottom_tab(name, tab=None, *, focus=True) -> tab | None
ui.toggle_bottom_panel(*, focus=True) -> bool
ui.hide_bottom_panel() / ui.focus_bottom_panel() / ui.blur_bottom_panel()
ui.is_bottom_panel_visible(tab_name=None) -> bool
ui.get_bottom_tab(name) -> tab | None
ui.list_bottom_tabs() -> list[str]
ui.bottom_nmap(key, plug_name)
ui.log_output  # LogOutputTab instance (append log lines programmatically)

# Picker helpers
ui.close_picker() -> None

# Sidebar queries
ui.get_sidebar_panel(name: str) -> Any
ui.active_sidebar_panel_name() -> str | None
ui.show_active_sidebar_panel(*, focus: bool = True) -> Any
```

---

### `syntax` — Tree-sitter Access

```python
syntax.get_tree(buffer: Buffer) -> SyntaxNode | None
syntax.query(buffer: Buffer, query: str) -> list[tuple[str, SyntaxNode]]
syntax.node_at(buffer: Buffer, line: int, col: int) -> SyntaxNode | None
syntax.get_injected_tree(buffer: Buffer, line: int, col: int) -> SyntaxNode | None
syntax.register_injection(parent_lang: str, query: str, child_lang: str) -> None
syntax.register_indent_rule(lang: str, query: str, indent_fn: Callable) -> None

# Fold provider registration
syntax.register_fold_provider(
    name: str,
    fn: Callable[[Buffer], list[FoldRange]],
    priority: int = 0,
) -> None
```

---

### `options` — Options System

```python
options.get(name: str) -> Any          # scope param planned
options.set(name: str, value: Any) -> None  # scope/buffer/window params planned

# Allow plugins to register their own options (appear in :set completion)
options.define(
    name: str,
    type_: type,               # note trailing underscore (avoids shadowing builtin)
    default: Any,
    scope: tuple[str, ...] = ('global',),  # e.g. ('global',) or ('global', 'buffer')
    doc: str = '',
    validator: Callable[[Any], bool] | None = None,
) -> None
```

---

### `registers` — Register Access

> **Note:** `RegistersAPI` class exists but has no methods yet. The interface below is the planned design.

```python
registers.get(name: str) -> RegisterContent | None
# RegisterContent: text, type ('char' | 'line' | 'block'), width (for block)

registers.set(name: str, text: str,
              type: Literal['char','line','block'] = 'char',
              width: int = 0) -> None

registers.list() -> dict[str, RegisterContent]
```

---

### `quickfix` — Quickfix and Location Lists

```python
@dataclass
class QFItem:
    path: Path | None
    buffer: Buffer | None
    line: int
    col: int
    text: str
    type: Literal['error', 'warning', 'info', ''] = ''

# Quickfix (global)
editor.quickfix.set(items: list[QFItem], title: str = '') -> None
editor.quickfix.get() -> list[QFItem]
editor.quickfix.next() -> None
editor.quickfix.prev() -> None
editor.quickfix.open() -> None
editor.quickfix.close() -> None

# Location list (per-window)
window.loclist.set(items: list[QFItem], title: str = '') -> None
window.loclist.next() -> None
window.loclist.prev() -> None
```

---

### `jumplist` — Jump List

```python
editor.jumplist.push(buffer: Buffer, line: int, col: int) -> None
editor.jumplist.back() -> None
editor.jumplist.forward() -> None
editor.jumplist.get() -> list[JumpEntry]
```

---

### `testing` — Test Runner Adapter API

Shared test runner infrastructure (neotest pattern). Concrete runners
(`peovim.plugins.pytest`, `peovim.plugins.cargo_test`, etc.) register here.

```python
@dataclass
class TestResult:
    id: str              # unique test identifier
    name: str
    status: Literal['passed', 'failed', 'skipped', 'running', 'unknown']
    output: str = ''     # captured stdout/stderr
    duration: float = 0.0
    file: Path | None = None
    line: int | None = None

testing.register_runner(
    name: str,
    # Is this runner applicable to this buffer?
    is_applicable: Callable[[Buffer], bool],
    # Discover all tests in a buffer/directory
    discover: Callable[[Buffer | Path], Awaitable[list[TestResult]]],
    # Run a subset of tests by id
    run: Callable[[list[str] | None], Awaitable[list[TestResult]]],
    # Optional: stop running tests
    stop: Callable[[], None] | None = None,
) -> None

# Run operations (dispatch to applicable runner)
testing.run_nearest(window: Window) -> Awaitable[None]  # test at cursor
testing.run_file(buffer: Buffer) -> Awaitable[None]
testing.run_all() -> Awaitable[None]
testing.stop() -> None

# Read results (shown as signs + diagnostic entries automatically)
testing.get_results(buffer: Buffer | None = None) -> list[TestResult]
testing.get_result(id: str) -> TestResult | None

# Events
# events: 'test_started', 'test_completed' (payload: TestResult)
```

---

### `diff` — Diff Mode

```python
# Enter diff mode: two or more windows showing the same file revisions side by side
diff.open(
    left: Buffer | str,   # buffer or git ref like 'HEAD~1'
    right: Buffer | str,
    direction: Literal['h', 'v'] = 'v',
) -> DiffHandle

diff.this(window: Window | None = None) -> None   # mark window for diff (like :diffthis)
diff.off(window: Window | None = None) -> None    # :diffoff

# Navigation
diff.next_hunk() -> None    # ]c
diff.prev_hunk() -> None    # [c

# Exchange hunks
diff.obtain() -> None       # do — pull change from other window
diff.put() -> None          # dp — push change to other window

diff.update() -> None       # recompute diff (after manual edits)
```

Diff decorations reuse the standard decoration system:
- `DiffAdd` highlight group on added lines
- `DiffDelete` on deleted/filler lines
- `DiffChange` on changed lines
- `DiffText` on the changed words within a changed line

---

## Plugin Entry Point and Lifecycle

### Module-level metadata (required)

```python
# ~/.config/peovim/plugins/my_plugin.py

PLUGIN_NAME = "my_plugin"        # used in error messages, which-key, :PluginList
PLUGIN_VERSION = "1.0.0"
REQUIRES: list[str] = []         # plugin names that must be loaded first
# e.g. REQUIRES = ["peovim.plugins.lsp"]
```

### Lifecycle functions

```python
def setup(api) -> None:
    """Called once when the plugin is loaded (or reloaded)."""
    api.events.on("buffer_saved", on_save)
    api.keymap.nmap('<leader>mp', run_thing, desc='My Plugin: run thing')
    api.commands.register('MyCommand', handle_command)

async def setup(api) -> None:
    """Async setup also supported — awaited before editor_ready fires."""
    await some_async_init()

def teardown(api) -> None:
    """Called on :PluginDisable, plugin reload, and editor exit.
    Must cancel background tasks, close subprocesses, flush stores."""
    background_task.cancel()
    subprocess_handle.kill()
```

### Plugin logger (do not use print() in plugins)

```python
log = api.get_logger("my_plugin")
log.debug("computed %d items", n)
log.info("attached to buffer %s", buf.name)
log.warning("unexpected response: %r", data)
log.error("fatal error", exc_info=True)
# Output viewable via :EdLog or peovim://log virtual buffer
# Never interrupts the TUI; does not call ui.notify()
```

### Error isolation contract

All plugin callbacks (event handlers, completion sources, keymap actions, syntax
providers) are wrapped by the editor in `try/except`. A plugin error:
- Is logged to `peovim://log`
- Shows a non-intrusive `ui.notify(level='error')` with plugin name
- **Never crashes the editor**
- Completion source errors: that source excluded from current round; others shown
- Syntax highlight errors: buffer falls back to plain text for that tick

### Lazy loading

```python
# init.py
plugins.load('peovim.plugins.gitsigns', on_event=['buffer_opened'])
plugins.load('peovim.plugins.lsp', on_filetype=['python', 'rust', 'go'])
plugins.load('peovim.plugins.dap', on_command=['Debug', 'Breakpoint'])
plugins.load('peovim.plugins.formatter', on_event=['buffer_pre_save'])
```
