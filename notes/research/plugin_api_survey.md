# Plugin API Survey: What Plugins Need from an Editor

This document surveys the Vim, Neovim, and VS Code plugin ecosystems to identify the full
set of editor API primitives a plugin system must expose. The goal is not to list plugins
but to extract the underlying capabilities they require — so the `peovim` plugin API can support
the same breadth.

Sources: awesome-neovim README, neovimcraft.com top plugins, tpope's catalog, ALE, nvim-dap,
neotest, telescope.nvim, gitsigns, nvim-treesitter, conform.nvim, VS Code extension API docs,
and direct inspection of ~50 individual plugin repositories.

---

## 1. Text Decorations

**What it is:** Overlaying visual information on buffer content without modifying the text.

**Plugins that need it:** gitsigns, nvim-cmp, indent-blankline, nvim-dap, neotest, ALE,
lsp_lines, trouble, hlslens, nvim-colorizer, render-markdown, barbecue, dropbar,
nvim-lightbulb, virtual-types, symbol-usage, tiny-inline-diagnostic, neogen, sniprun,
obsidian.nvim, nvim-ufo, markview, interestingwords, hlargs, rainbow-delimiters.

**Required primitives:**

- **Highlight ranges** — apply a named style (fg/bg/bold/italic/underline/strikethrough) to
  an arbitrary `(line, col_start, col_end)` span, across multiple lines, with priority
  ordering when spans overlap. Must survive buffer edits (extmark semantics: positions
  update with insertions/deletions).

- **Virtual text** — insert styled text at the end of a line, or inline between characters,
  that is not part of the buffer content and does not shift real characters. Needs:
  left-aligned, right-aligned, and overlay (replaces visual chars without changing buffer)
  variants. Multiple virtual texts per line must stack with priority.

- **Virtual lines** — insert entire synthetic lines (containing styled text) above or below a
  real buffer line. Used by lsp_lines to render multi-line diagnostics. These lines consume
  screen space but have no buffer content.

- **Gutter / sign column** — a fixed-width column to the left of line numbers where plugins
  place single-character symbols with fg/bg colors. Needs: register a sign type (symbol +
  colors), place/remove sign instances at a line number. Multiple signs per line with priority.

- **Inlay hints** — inline virtual text rendered within the line flow (e.g., type annotations
  between tokens). Distinct from end-of-line virtual text. LSP provides positions; the editor
  renders.

- **CodeLens** — an actionable virtual line above a function/class showing reference counts,
  test status, etc. Clicking/activating a CodeLens runs a command. VS Code has a dedicated
  provider API; Neovim implements this via virtual text + LSP.

- **Fold text replacement** — when a fold is closed, display a custom string instead of the
  folded content. Controlled by a per-fold or global callback.

- **Conceal** — replace a pattern of characters with a single substitute character (or
  nothing) for display only. Used by markdown, org-mode, LaTeX plugins to hide syntax
  markers. The underlying buffer is unchanged.

- **Colorcolumn** — highlight the cell at a specific column across all lines. Used for line
  length guides.

- **Cursorline / cursorcolumn** — highlight the line and/or column the cursor is on.
  Plugins may want to disable or override these contextually.

**API shape needed:**
```python
# Extmark-style: returns an ID, survives edits
deco_id = buffer.add_highlight(line, col_start, col_end, style, priority=0, ns="myplugin")
buffer.remove_highlight(deco_id)

vtext_id = buffer.add_virtual_text(line, text, style, placement="eol"|"inline"|"overlay",
                                   col=None, priority=0, ns="myplugin")
vline_id = buffer.add_virtual_line(after_line, segments: list[StyledText], ns="myplugin")

sign_id = buffer.add_sign(line, sign_type, priority=0, ns="myplugin")
editor.register_sign_type(name, symbol, fg, bg)

# Clear all decorations from a namespace (used on re-render)
buffer.clear_namespace(ns)
```

---

## 2. UI Elements

### 2a. Floating Windows / Popups

**Plugins:** nvim-cmp, telescope, LSP hover, lspsaga, trouble, which-key, gitsigns blame,
nvim-dap widgets, neotest output, goto-preview, actions-preview, noice, dashboard, snacks
notifier, toggleterm float, rest.nvim results.

**Required primitives:**
- Create a floating window at an absolute screen position or relative to a buffer position
  (anchor to cursor, or to a specific cell). Specify: width, height, border style, title,
  footer, z-order (floats stack).
- Float contents are a buffer — the same buffer/window model applies inside a float.
- Close float programmatically or on `<Esc>` / focus loss.
- Scrollable float content (floats showing long hover docs, etc.).
- Non-focusable floats (decorative overlays, notifications that don't steal input).
- Focus management: a float can capture input; pressing `<Esc>` returns focus to the
  originating window.

### 2b. Split Windows / Panels

**Plugins:** NERDTree, neo-tree, nvim-tree (sidebar), diffview (tabpage layout), toggleterm
(horizontal split), nvim-dap (repl, locals pane), outline.nvim (right panel), neogit (status
buffer in split).

**Required primitives:**
- Horizontal / vertical splits: create a new window pane displaying a given buffer.
- Control split direction, initial size, and which window gets focus after split.
- Convert a split to a fixed-width sidebar (non-resizable or anchored).
- Close a specific window without affecting its buffer.
- `only()` — close all windows except the active one.
- Query: list of open windows, their sizes and positions, which window is focused.

### 2c. Status Bar

**Plugins:** lualine, vim-airline, heirline, mini.statusline, incline (per-window float bar),
nvim-navic (breadcrumb in statusline), gitsigns (hunk counts), lsp-status (progress in bar),
arrow (marks in bar).

**Required primitives:**
- The status bar is a programmable region: plugins register components (functions that return
  styled text segments), placed in left/center/right zones.
- Components can be: static text, dynamic callbacks, and conditional (hide if window narrow).
- Per-window status bars (one per window, not just one global bar) — used by incline and
  multi-window setups.
- Winbar: a second bar at the top of each window (used by barbecue, dropbar for breadcrumbs).
- Tabline: a bar across the top of the terminal showing tabs/buffers.

### 2d. Tab Line / Buffer Line

**Plugins:** bufferline.nvim, vim-airline tabs, mini.tabline.

**Required primitives:**
- Render a row of clickable tab/buffer names with icons, modified indicators, close buttons.
- Plugins need to: provide the list of entries, their labels, styles, and click handlers.

### 2e. Command Line / Prompt

**Plugins:** noice (replaces cmdline UI entirely), which-key (shows completions in cmdline),
telescope (uses its own input field), fzf-lua, mini.pick.

**Required primitives:**
- Intercept / override the `:` command-line rendering. A plugin should be able to replace
  the default command-line area with a custom-rendered widget.
- Read the current command-line contents programmatically.
- Inject completions into the command-line completion popup.
- `vim.ui.input()` equivalent: show a prompt, collect a string, return it asynchronously.
- `vim.ui.select()` equivalent: show a list, user picks one, return choice.

### 2f. Notifications

**Plugins:** fidget (LSP progress), nvim-notify, snacks notifier, noice.

**Required primitives:**
- `notify(message, level, opts)` — show a transient message. Levels: debug/info/warn/error.
- Notifications are non-blocking; they appear in a corner and auto-dismiss after a timeout.
- Plugins can replace the default notify implementation (so a custom UI can intercept all
  notifications from the editor and other plugins).
- Progress notifications: a notification with a spinner that can be updated or dismissed.

### 2g. Startup Screen / Dashboard

**Plugins:** dashboard-nvim, vim-startify, mini.starter, snacks.dashboard.

**Required primitives:**
- Hook into editor startup (no files open) to render a custom buffer.
- Display static text, ASCII art, clickable shortcuts.
- The buffer is a normal buffer with custom highlights; no special widget system needed.

### 2h. Input Method / IME

**Plugins:** vietnamese.nvim, Korean-IME.nvim.

**Required primitives:**
- Intercept individual keystrokes in insert mode and replace them with composed characters
  before they reach the buffer. This is a keystroke transform layer, not a full IME.

---

## 3. Buffer Manipulation

**Plugins:** conform, ALE fixer, neogen, nvim-surround, vim-surround, commentary, autopairs,
rest.nvim, obsidian, oil.nvim (filesystem-as-buffer), dadbod, sniprun, coderunner,
LuaSnip/UltiSnips (snippet expansion), avante (inline AI edits).

**Required primitives:**

- **Read operations:**
  - `buffer.get_text(line_start, col_start, line_end, col_end) -> str`
  - `buffer.get_line(n) -> str`
  - `buffer.line_count() -> int`
  - `buffer.get_selection() -> (start, end, text)` — current visual selection
  - `buffer.filetype`, `buffer.path`, `buffer.encoding`, `buffer.is_modified`
  - `buffer.get_option(name)` — buffer-local options

- **Write operations:**
  - `buffer.insert(line, col, text)` — must integrate with undo stack
  - `buffer.delete(line_start, col_start, line_end, col_end)` — must be undoable
  - `buffer.replace_range(start, end, new_text)` — atomic replace (used by formatters)
  - `buffer.apply_edits(list[TextEdit])` — batch LSP-style edits, applied in reverse order to
    preserve offsets; the fundamental primitive for formatters and LSP code actions
  - All mutations must be undoable as a group if done within a single "compound edit" scope.

- **Cursor and selection:**
  - `window.cursor -> (line, col)`
  - `window.set_cursor(line, col)`
  - `window.get_visual_selection() -> (start, end)`
  - `window.set_visual_selection(start, end)`

- **Buffer lifecycle:**
  - `editor.open_buffer(path) -> Buffer`
  - `editor.new_buffer(name=None) -> Buffer` — scratch buffer
  - `editor.close_buffer(buffer, force=False)`
  - `editor.list_buffers() -> list[Buffer]`
  - `buffer.save()`, `buffer.save_as(path)`
  - `buffer.reload()` — re-read from disk

- **Buffer content as a writable view:**
  - oil.nvim requires editing the filesystem by editing a buffer where each line represents
    a file. On save, the editor diffs the before/after text and applies filesystem operations.
    This needs: create a buffer backed by a virtual document (not a real file) with a custom
    save handler.

---

## 4. Keyboard / Input Interception

**Plugins:** autopairs (intercepts `(`, `[`, `{`, `<CR>`, `<BS>`), nvim-cmp (intercepts
`<Tab>`, `<CR>`, `<C-n>`, `<C-p>`), LuaSnip/UltiSnips (intercepts `<Tab>` for placeholder
navigation), leap/flash (intercepts multi-char sequences), which-key (intercepts any partial
sequence to show hints), noice (intercepts `:` to replace cmdline UI), vim-dispatch (`:Make`
command), all modal motion plugins.

**Required primitives:**

- **Key mapping registration:** bind a key sequence in a specific mode to a function. The
  function receives the editor state and returns an action (or executes imperatively).
  Must support: `nmap`, `imap`, `vmap`, `xmap`, `omap`, `tmap` (terminal mode), `cmap`
  (command mode).

- **Key mapping with fallthrough:** a binding can "pass through" to the next handler if it
  decides not to handle the key (used by autopairs: check context, then either insert pair
  or pass through to normal `(`).

- **`<expr>` mappings:** the RHS of the mapping is a function that returns a key sequence
  string to replay, rather than an action. Used by completion plugins for `<Tab>` logic.

- **Operator-pending mode:** a plugin can register a new operator (e.g., `ys` for surround)
  that waits for a motion or text object, then acts on the resulting range.

- **Text object registration:** plugins can register new text objects (inner/outer pairs)
  that are available as arguments to operators. E.g., `nvim-treesitter-textobjects` registers
  `af` (function body) as a text object.

- **Motion registration:** plugins can register new motions (movements) that appear in normal
  and operator-pending mode.

- **Insert mode key interception with context:** autopairs must know: what character is before
  the cursor, what character is after, whether we are inside a string or comment (requires
  tree-sitter or basic bracket tracking). The plugin needs read access to buffer content +
  cursor position at key dispatch time.

- **Terminal mode key bindings:** for toggleterm and other terminal integrations, bindings
  must work inside the terminal buffer (separate mode from normal/insert).

- **Timeout / partial match:** when a key sequence is partially matched (e.g., typed `g` but
  `gg`, `gd`, `gf` etc. are registered), the engine waits for the next key. The timeout
  duration should be configurable. This is already part of the modal engine but plugins
  need to hook into it cleanly.

- **Leader key:** `<leader>` is a user-configured prefix key. All plugins use it. Must be
  substituted transparently at binding registration time.

- **`<Plug>` mappings:** a stable internal name for a plugin action, so users can remap it
  without knowing the implementation. E.g., `keymap.nmap('<leader>gc', '<Plug>Commentary')`.

---

## 5. External Process Integration

**Plugins:** ALE, conform, nvim-lint, vim-dispatch, vim-test, neotest, sniprun, toggleterm,
rest.nvim (uses curl), gitsigns (git commands), neogit, telescope (ripgrep, fd), fzf-lua,
markdown-preview (Node server), mason (installs tools), copilot, avante (API calls),
LSP client (language servers), nvim-dap (debug adapters), database plugins.

**Required primitives:**

- **Async subprocess:** spawn an external process with args, env, cwd, stdin. Receive stdout
  and stderr asynchronously as they arrive. On completion receive exit code. Must not block
  the editor event loop. This is the single most important external integration primitive.
  ```python
  job = editor.spawn(
      cmd=["ruff", "check", "--stdin-filename", path, "-"],
      stdin=buffer_text,
      cwd=workspace_root,
      on_stdout=handle_output,
      on_stderr=handle_errors,
      on_exit=handle_done,
  )
  job.kill()
  ```

- **Synchronous subprocess (for formatters only):** some formatters (prettier, rustfmt) are
  invoked synchronously in a thread to format the buffer before saving. The result replaces
  buffer content. This should run in a thread pool, not blocking the main loop.

- **LSP subprocess:** JSON-RPC over stdio. The editor's built-in LSP client handles the
  protocol, but plugins need to:
  - Start an LSP server for a filetype
  - Attach a running server to a buffer
  - Register custom LSP request handlers
  - Make ad-hoc LSP requests from a plugin
  - Receive LSP notifications (diagnostics pushed by server)

- **HTTP client:** rest.nvim, obsidian (note sync), AI plugins all make HTTP requests. The
  editor should provide or allow plugins to use an async HTTP primitive (or just let them use
  Python's `aiohttp` / `httpx` since the config is Python).

- **Terminal buffer:** a buffer backed by a running process (shell, REPL, test runner).
  Input typed in the buffer goes to the process stdin; process stdout is rendered in the
  buffer. The editor's terminal mode handles this. Plugins need:
  - Create a terminal buffer running a given command
  - Send text to a terminal buffer programmatically (for `vim-test`, `sniprun`)
  - Detect when a terminal process exits
  - Resize the terminal when the window size changes

- **File watching:** watch a path or directory for changes; fire a callback when a file is
  created/modified/deleted. Used by gitsigns (watches `.git/HEAD` and index), neotest
  (watch mode), livegrep. This should use the OS-level file watch API (inotify/FSEvents/
  ReadDirectoryChangesW) via Python's `watchdog` or asyncio.

- **Network / IPC:** a few plugins (markdown-preview WebSocket server, remote development
  plugins) need to open a server socket. This is advanced; the editor does not need to
  provide this, but it must not prevent plugins from using Python's standard `asyncio`
  networking.

---

## 6. Event System

**Plugins:** virtually every plugin subscribes to events. gitsigns (BufEnter, BufWritePost),
lualine (CursorMoved, BufEnter, WinEnter), ALE (TextChanged, InsertLeave, BufWritePost),
nvim-cmp (TextChangedI, InsertEnter), autopairs (InsertEnter), telescope (BufLeave to close),
which-key (key input events), dashboard (VimEnter).

**Required primitives:**

Core editor events plugins must be able to subscribe to:

| Event | When it fires |
|-------|--------------|
| `buffer_opened(buffer)` | A new buffer is loaded into memory |
| `buffer_closed(buffer)` | A buffer is removed from the buffer list |
| `buffer_entered(buffer, window)` | Cursor moves into a window displaying this buffer |
| `buffer_left(buffer, window)` | Cursor leaves a window displaying this buffer |
| `buffer_changed(buffer, change)` | Text was inserted/deleted (fires per-edit) |
| `buffer_saved(buffer)` | Buffer written to disk (`:w`) |
| `buffer_pre_save(buffer)` | Before write; plugin can cancel or modify (for formatters) |
| `filetype_detected(buffer, filetype)` | Filetype set/changed |
| `cursor_moved(window)` | Cursor position changed in normal or visual mode |
| `cursor_moved_insert(window)` | Cursor moved in insert mode |
| `insert_entered(window)` | Entered insert mode |
| `insert_left(window)` | Left insert mode |
| `mode_changed(from_mode, to_mode)` | Any mode transition |
| `window_entered(window)` | Focus moved to a window |
| `window_resized(window)` | Window size changed |
| `window_closed(window)` | A window was closed |
| `tab_entered(tab)` | Switched to a tab page |
| `editor_startup()` | All plugins loaded, first render about to happen |
| `editor_shutdown()` | Editor is exiting |
| `colorscheme_changed(name)` | Theme changed |
| `option_changed(name, old, new, scope)` | `:set` option changed |
| `diagnostic_updated(buffer)` | Diagnostic list changed for a buffer |
| `lsp_attached(buffer, server)` | LSP server attached to buffer |

**Event API:**
```python
@editor.on("buffer_saved")
def run_formatter(buffer):
    ...

# Or with decorator + unsubscribe
token = editor.on("cursor_moved", handler)
editor.off(token)

# Autocmd-style: trigger for specific filetypes / patterns
@editor.on("buffer_opened", pattern="*.py")
def setup_python(buffer):
    ...
```

**User-defined events:** plugins should be able to fire custom events and let other plugins
subscribe. This enables plugin-to-plugin communication without direct imports.
```python
editor.emit("my_plugin.result_ready", data=result)
editor.on("my_plugin.result_ready", handler)
```

---

## 7. Language Intelligence (LSP)

**Plugins:** nvim-lspconfig, lspsaga, nvim-cmp (cmp-nvim-lsp source), trouble,
nvim-lightbulb, lsp_signature, fidget, none-ls, conform (LSP format), aerial, outline,
nvim-navic, barbecue, nvim-ufo (LSP folding ranges), rustaceanvim, clangd_extensions,
haskell-tools, nvim-jdtls, nvim-java, nvim-metals, ALE.

**Required primitives:**

The editor ships a built-in async LSP client. Plugins interact with it via:

- **Server registration:**
  ```python
  lsp.register_server(
      name="pylsp",
      cmd=["pylsp"],
      filetypes=["python"],
      root_markers=["pyproject.toml", "setup.py"],
      settings={...},
      capabilities_override={...},
  )
  ```

- **Attach / detach:** plugins can attach a server to a buffer manually, or let the auto-
  attach mechanism handle it based on filetype.

- **Request / notify:** make ad-hoc LSP requests from a plugin.
  ```python
  result = await lsp.request(buffer, "textDocument/hover", params)
  lsp.notify(buffer, "workspace/didChangeConfiguration", settings)
  ```

- **Response handlers:** register a handler for specific LSP methods. Allows plugins like
  lspsaga to replace the default hover/definition UI.
  ```python
  lsp.override_handler("textDocument/hover", my_fancy_hover_ui)
  ```

- **Diagnostic access:**
  ```python
  diags = editor.diagnostics.get(buffer)  # list of Diagnostic
  editor.diagnostics.set(buffer, diags, source="my_linter")
  ```
  Diagnostics have: range, severity (error/warn/info/hint), message, code, source, tags
  (deprecated, unnecessary). Plugins (like none-ls, nvim-lint) can inject diagnostics from
  non-LSP sources using the same API.

- **Completion items:** the completion engine queries all registered sources. LSP is one
  source; plugins can register additional sources (buffer words, file paths, snippets,
  git commits, emoji, etc.).

- **Code actions:** plugins can register custom code action providers.

- **Semantic tokens:** LSP servers push semantic token data. The editor applies it as
  highlights. Plugins can read or augment the token data.

- **Workspace symbol / document symbol:** used by outline, aerial, Telescope symbol picker.

- **Inlay hints:** LSP 3.17+ feature. Server pushes hint positions; editor renders them as
  virtual text. Plugins like nvim-lsp-endhints, virtual-types customize the display.

---

## 8. Completion Engine

**Plugins:** nvim-cmp, blink.cmp, coq_nvim, mini.completion, copilot, avante, cmp-* sources.

**Required primitives:**

The completion system is a pipeline: trigger detection → source query → item ranking →
UI rendering. Plugins need to plug into each stage.

- **Source registration:**
  ```python
  completion.register_source(
      name="buffer_words",
      trigger_characters=[],   # always active
      complete=async_complete_fn,  # (context) -> list[CompletionItem]
      resolve=async_resolve_fn,    # (item) -> CompletionItem with docs filled in
      priority=100,
  )
  ```
  `context` includes: buffer, cursor position, current word prefix, trigger character,
  trigger kind (manual vs. automatic).

- **CompletionItem fields:** label, insert text, kind (function/class/variable/keyword/
  snippet/file/color/...), detail, documentation (string or MarkupContent), sort text,
  filter text, text edit (to handle imports inserted elsewhere), additional text edits,
  commit characters, tags (deprecated), data (opaque, for resolve).

- **Trigger conditions:** sources can specify trigger characters (LSP does this), or be
  always-active. The engine also supports manual trigger (Ctrl-Space equivalent).

- **Item insertion:** on confirmation, the engine calls the item's text edit. For snippets,
  it hands the snippet body to the snippet engine for expansion. For LSP items with
  `additionalTextEdits` (auto-imports), it applies those separately.

- **Documentation popup:** the completion UI shows a floating window with the selected
  item's documentation. Plugins can customize the rendering.

- **Ghost text / inline preview:** show the first completion's insert text as ghost text
  in the buffer (like Copilot). Requires the virtual text inline placement primitive.

---

## 9. Snippet Engine

**Plugins:** LuaSnip, UltiSnips, nvim-snippy, mini.snippets, friendly-snippets (data).

**Required primitives:**

- **Snippet definition:** a snippet has a trigger word, a body with tabstop placeholders
  `$1`, `$2`, ... `$0` (final position), mirror nodes (where `$1` text is duplicated),
  choice nodes (a dropdown of options at a position), and transform nodes (computed from
  other nodes via regex/function).

- **Expansion:** when triggered, the snippet body is inserted into the buffer at the cursor,
  with tabstop positions tracked as extmarks (so they survive edits).

- **Navigation:** `<Tab>` / `<S-Tab>` moves between placeholders. The snippet engine manages
  a stack of active snippets (nested snippets are supported).

- **Placeholder editing:** while inside a placeholder, edits are reflected in all mirrors.

- **Snippet loading:** snippets are loaded from files (VSCode format `.json`, UltiSnips
  `.snippets`, Lua-defined). The engine needs file I/O access and a way to associate snippets
  with filetypes.

- **Dynamic snippets:** snippet bodies can be functions that receive context (current
  filename, date, selected text) and return the body string.

---

## 10. Fuzzy Finder / Picker

**Plugins:** telescope.nvim, fzf-lua, mini.pick, snacks.picker, harpoon UI, fzfx.

**Required primitives:**

The picker is a complex but essential UI component. Plugins need to either use a built-in
picker API or build their own from lower-level primitives (floating window + buffer).

- **Open a picker:**
  ```python
  picker = editor.open_picker(
      title="Find Files",
      source=async_generator_fn,     # yields items as they are found
      format_item=fn,                # item -> DisplayItem (label, highlights, preview)
      on_confirm=fn,                 # called with selected item(s)
      on_close=fn,
      multi_select=False,
      preview=preview_fn,            # item -> buffer or text to show in preview pane
      initial_query="",
  )
  ```

- **Source types:** the source can be: a list (static), an async generator (streaming, e.g.,
  ripgrep stdout), or a function of (query_string) → items (for fuzzy filtering server-side).

- **Item format:** each item has a display string (with optional highlight spans), a sort key,
  and an arbitrary data payload.

- **Preview pane:** the picker has an optional preview pane that shows a buffer (or custom
  rendered content) for the currently selected item. For file pickers, this is a read-only
  buffer of the file; for grep results, the file is opened at the matching line.

- **Key bindings inside the picker:** plugins register custom actions for keys within the
  picker context (e.g., `<C-v>` to open in vertical split, `<C-t>` for tab).

- **Sorting / scoring:** the picker uses a fuzzy-matching score to rank items. Plugins can
  provide a custom sorter.

---

## 11. File System / Workspace

**Plugins:** telescope (file finder), oil.nvim, neo-tree, nvim-tree, project.nvim,
workspaces.nvim, obsidian (vault traversal), neogit (git root), LSP (workspace folders),
vim-projectionist, nvim-config-local.

**Required primitives:**

- **Working directory:** `editor.cwd()` → str. `editor.set_cwd(path)`. Per-tab or per-window
  cwd overrides. Plugins (project.nvim, rooter) detect the project root and set cwd.

- **Workspace roots:** a list of root directories currently "open." LSP uses these as
  workspace folders. Plugins add roots when opening a project.

- **File system access:** plugins use Python's `pathlib` / `os` directly. The editor does
  not need to abstract this. However, the editor should provide:
  - `editor.find_files(pattern, cwd, respect_gitignore=True) -> AsyncGenerator[Path]`
  - `editor.grep(pattern, cwd, file_glob="*") -> AsyncGenerator[GrepMatch]`
  These are used by telescope, fzf, spectre, etc. and benefit from a shared, fast
  implementation (ripgrep-backed).

- **Root detection:** `editor.find_root(path, markers=["pyproject.toml", ".git"]) -> Path`.
  Returns the nearest ancestor directory containing any marker file.

- **File watching:** `editor.watch(path, callback, recursive=False) -> WatchHandle`.
  Fires callback on create/modify/delete. Used by gitsigns, neotest watch mode, live reload.

---

## 12. Git Integration

**Plugins:** vim-fugitive, gitsigns, neogit, diffview, vim-rhubarb, mini.git, snacks.git.

Git is accessed primarily by shelling out to `git` (via the async subprocess primitive).
However, several higher-level hooks are useful:

- **Git root discovery:** `git.root(path) -> Path | None`. Used by many plugins.

- **Buffer git status:** `git.status(buffer) -> GitStatus` — is this file tracked, staged,
  modified, untracked? The line-level hunk data (added/removed/changed line ranges) is used
  by gitsigns for sign column decorations.

- **Hunk access:** `git.get_hunks(buffer) -> list[Hunk]`. Each hunk is a `(old_start,
  old_lines, new_start, new_lines)` with the diff text. Used for staging hunks, previewing
  changes.

- **Blame:** `git.blame_line(buffer, line) -> BlameInfo`. Asynchronous. Returns commit hash,
  author, date, summary. Used by gitsigns virtual text blame.

- **Remote URL:** `git.remote_url(root) -> str`. Used by vim-rhubarb to open GitHub URLs.

These can all be implemented as thin wrappers over async `git` subprocesses. The point is
that the editor should provide this as a first-class API so multiple plugins share one
implementation rather than each spawning their own git processes.

---

## 13. Navigation

**Plugins:** leap, flash, hop, telescope, harpoon, marks.nvim, grapple, trailblazer,
mini.jump, mini.jump2d, nvim-hlslens (search result navigation), aerial (symbol jump).

**Required primitives:**

- **Jump list:** `editor.jumplist.push(buffer, line, col)`, `editor.jumplist.back()`,
  `editor.jumplist.forward()`. The jump list is already Vim-standard; plugins must be able
  to push entries to it when they teleport the cursor.

- **Change list:** per-buffer list of cursor positions where changes occurred. `g;` / `g,`
  navigation. Plugins that make programmatic edits should add to the change list.

- **Global marks:** marks that persist across sessions and can jump to a different file.
  Harpoon and grapple use this to store project-specific file shortcuts.

- **Quickfix list:** `editor.quickfix.set(items: list[QFItem])`. Each item: buffer/file,
  line, col, text, type (error/warning/info). Used by ALE, compilers, grep results,
  telescope quickfix send. `editor.quickfix.next()`, `editor.quickfix.prev()`.

- **Location list:** per-window quickfix list. Distinct from the global quickfix.

- **Tag stack:** for `Ctrl-]` / `Ctrl-o` tag navigation. Plugins can push entries.

- **Cursor teleport:** `window.set_cursor(line, col)` + `window.scroll_to(line)` with
  optional centering. Plugins must be able to move the cursor and scroll the view.

- **Motion registration:** plugins register new motions that appear alongside the built-in
  `w`, `b`, `e`, etc. The motion receives (window, count) and returns a new cursor position.
  Operator-pending mode then uses the motion for the operator range.

---

## 14. Debugging (DAP)

**Plugins:** nvim-dap, nvim-dap-ui, neotest (DAP integration), nvim-java (DAP config),
vim-vscode (DAP compatibility).

The Debug Adapter Protocol is a separate protocol (like LSP but for debuggers). The editor
or a plugin must implement a DAP client. Plugins need:

- **Breakpoint management:** `debug.toggle_breakpoint(buffer, line)`, `debug.list_breakpoints()`,
  `debug.clear_all_breakpoints()`. Breakpoints are stored persistently (across sessions).
  They are rendered as signs in the gutter.

- **Session lifecycle:** `debug.start_session(config)` — launches or attaches to a debug
  adapter. `debug.stop_session()`. The config specifies the adapter type, executable, args,
  and language-specific settings.

- **Execution control:** `debug.continue()`, `debug.step_over()`, `debug.step_into()`,
  `debug.step_out()`, `debug.pause()`.

- **State inspection:** on pause, the DAP client receives the current thread, stack frames,
  and local variables. Plugins display this in floating windows or dedicated split panes.
  APIs needed: `debug.get_stack_frames()`, `debug.get_variables(scope_ref)`,
  `debug.evaluate(expression)`.

- **Execution position decoration:** highlight the current execution line with a sign and
  highlight. This requires the standard sign/highlight decoration primitives.

- **REPL buffer:** an interactive buffer where the user types expressions and sees results.
  This is a special terminal-like buffer connected to the DAP debug console.

- **Adapter registration:** `debug.register_adapter(type, config)` — plugins register adapter
  types (codelldb, debugpy, jdtls debugger, etc.).

---

## 15. Testing Framework Integration

**Plugins:** neotest, vim-test, nvim-coverage.

**Required primitives:**

- **Test discovery:** a plugin parses the buffer (using tree-sitter) to find test functions.
  Returns a tree of test suites and test cases with their buffer positions.

- **Test execution:** run one test, a file, or the whole suite via async subprocess. Stream
  output to a results buffer.

- **Result annotation:** mark test lines with pass/fail signs and virtual text showing
  results. Failed tests show the error message as virtual text or in a floating window.

- **Diagnostic injection:** test failures are injected as diagnostics so they appear in the
  trouble list, the quickfix list, and inline.

- **Watch mode:** file watcher triggers re-run on buffer save. Requires the file watch API.

---

## 16. Session / State Persistence

**Plugins:** vim-obsession, mini.sessions, projections.nvim, suave.lua, auto-session.

**Required primitives:**

- **Session save:** serialize the editor state to a file. State includes: open buffers (by
  path), window layout (split tree), cursor positions per window, marks, folds, local options,
  and register contents. The session file is the editor's native format; plugins augment it.

- **Session restore:** read a session file and restore the full state.

- **Plugin session data:** plugins can register a save/restore hook to include their own
  state in the session. E.g., harpoon saves its mark list, which-key saves custom groups,
  nvim-dap saves breakpoints.
  ```python
  @session.register_handler("my_plugin")
  def save() -> dict:    return {"marks": ...}
  def load(data: dict):  ...
  ```

- **Persistent storage:** beyond sessions, plugins need a place to store data between
  invocations. The editor should expose:
  ```python
  store = editor.get_store("my_plugin")  # key-value, backed by JSON or SQLite
  store.set("key", value)
  store.get("key", default)
  ```
  Used by: harpoon (file marks), bookmarks plugins, frecency trackers, macro storage,
  snippets, session managers.

---

## 17. Syntax / Parsing

**Plugins:** nvim-treesitter, indent-blankline (scope detection), nvim-ufo (folding),
neogen (docstring generation), neotest (test discovery), nvim-treesitter-textobjects,
jsx-element, hlargs, rainbow-delimiters, orgmode, neorg, markview.

**Required primitives:**

- **Tree-sitter access:** for every buffer with a supported language, the editor maintains
  an up-to-date parse tree. Plugins query it:
  ```python
  tree = buffer.get_syntax_tree()          # returns the root node
  nodes = buffer.query_syntax(            # returns list of (capture_name, node)
      "(function_definition name: (identifier) @fn_name) @fn"
  )
  ```

- **Language injection:** some buffers contain multiple languages (Markdown with code fences,
  HTML with embedded JS/CSS, Vue SFCs). The tree-sitter injection system parses each
  language region separately. Plugins access the injected tree for a given region.

- **Text object ranges from tree:** given a node or a query capture, return the
  `(start_line, start_col, end_line, end_col)` range it spans. This is the primitive
  that textobjects plugins use to select `af` (a function).

- **Scope detection:** given a cursor position, find the enclosing scope node. Used by
  indent-blankline (to highlight the active scope), mini.indentscope, and code folding.

- **Indent from tree:** compute the correct indentation for the next line after a token.
  Tree-sitter-based indentation (experimental in Neovim but widely used).

- **Filetype detection:** `editor.detect_filetype(path, first_line) -> str`. Uses extension,
  shebang, content heuristics. Plugins can register additional detection rules.

- **Folding from tree:** compute fold ranges from the syntax tree. Plugins register a fold
  provider that returns `list[(start_line, end_line, kind)]`.

---

## 18. Options and Configuration

**Plugins:** vim-sleuth (auto-detects indent settings), editorconfig plugins, local config
plugins (nvim-config-local), swenv (Python venv switcher), whichpy.

**Required primitives:**

- **Option read/write:** `editor.get_option(name)`, `editor.set_option(name, value)`.
  Options have scopes: global, window-local, buffer-local. A plugin sets a buffer-local
  option on a specific buffer without affecting other buffers.

- **Option change events:** `editor.on("option_changed", handler)` — fired when any option
  is changed (via `:set` or API). Used by plugins that react to option changes.

- **Custom options:** plugins can register their own options that appear in `:set` and can
  be configured in `init.py`. This lets plugin behavior be controlled uniformly via the
  standard options mechanism.

- **Filetype-local configuration:** run code when a buffer with a specific filetype is opened.
  The standard pattern is `editor.on("filetype_detected", pattern="python", handler)`.

- **EditorConfig support:** read `.editorconfig` files from the directory hierarchy and apply
  `indent_size`, `tab_width`, `end_of_line`, `charset` etc. as buffer-local options.

---

## 19. UI Intercept / Override Hooks

**Plugins:** noice (replaces cmdline, messages, popupmenu), which-key (intercepts keymap
display), custom completion UI (replaces default popup menu).

Some plugins want to completely replace a built-in editor UI element with a custom
implementation. This requires:

- **UI hook points:** the editor fires an event/calls a hook before rendering each UI
  component. A plugin can intercept and provide a replacement.

  Key intercept points:
  - Command-line input area (`:`, `/`, `?`)
  - Command-line completion popup
  - Message area (where `:echo`, errors, and `:messages` appear)
  - `vim.ui.input()` — replace the default input prompt
  - `vim.ui.select()` — replace the default selection menu
  - Completion popup menu (the floating list of completion items)
  - Hover popup (LSP hover)

- **`vim.ui.input` / `vim.ui.select` equivalents:** the editor defines standard async
  functions for collecting user input and selection. All built-in code uses these functions
  so that plugins can override them with custom UIs (e.g., telescope-powered `ui.select`).

---

## 20. Remote / Embedded Editing

**Plugins:** nvim-dadbod-ui (database in editor), rest.nvim, s3edit.nvim, oil.nvim
(filesystem as buffer), otter.nvim (embedded language LSP).

**Required primitives:**

- **Virtual document provider:** create a buffer whose content is managed by a plugin
  (not backed by a real file). The plugin provides the initial content, handles saves
  (`:w` calls the plugin's save handler), and handles reloads.
  ```python
  buffer = editor.create_virtual_buffer(
      name="[DB Results]",
      content_provider=my_provider,    # callable: () -> str
      save_handler=my_save_fn,         # callable: (new_text: str) -> None
      filetype="sql",
      readonly=True,
  )
  ```

- **Custom filetype handlers:** when a file with a given extension is opened, a plugin
  intercepts before the normal load, fetches the content from a remote source (S3, database,
  HTTP), and presents it in a buffer. On save, the plugin writes back to the remote source.

---

## 21. Color / Theme System

**Plugins:** nvim-colorizer, ccc, color scheme plugins, colortils, mini.colors, lush, base16.

**Required primitives:**

- **Color scheme loading:** `editor.set_colorscheme(name)`. The scheme defines highlight
  groups (named styles used throughout the editor and by plugins). Plugins reference named
  groups, not raw colors, so they adapt to any theme automatically.

- **Highlight group definition:**
  ```python
  editor.set_highlight("MyPluginWarning", fg="#ffaa00", bold=True, italic=False)
  editor.set_highlight("MyPluginError", link="DiagnosticError")  # link to existing group
  ```

- **Highlight group query:** `editor.get_highlight(name) -> HighlightStyle`. Plugins (like
  tiny-devicons-auto-colors) read existing highlight groups to derive colors.

- **Colorscheme change event:** `editor.on("colorscheme_changed", handler)` — plugins
  that cache colors must refresh their cache when the theme changes.

- **Semantic token highlights:** LSP semantic tokens are mapped to highlight groups. Plugins
  can override the default mapping.

---

## 22. Macro / Repeat Integration

**Plugins:** vim-repeat (allows plugin operations to be repeated with `.`), macro-recording
plugins, macrothis.

**Required primitives:**

- **Dot-repeat registration:** a plugin can declare that its last operation should be
  repeatable with `.`. It provides the action dataclass that was executed; the modal engine
  replays it on `.`.

- **Macro recording:** the editor records keystrokes into a register. Plugins whose actions
  are triggered by keystrokes are automatically recorded. But plugins that act programmatically
  need to register their action with the recorder so macros replay correctly.

---

## 23. Clipboard / Register Integration

**Plugins:** nvim-neoclip, registers.nvim, nvim-peekup, karen-yank.

**Required primitives:**

- **Register read/write:** `editor.registers.get(name) -> RegisterContent`,
  `editor.registers.set(name, text, type)`. Type: char, line, or block (for blockwise yank).

- **System clipboard:** `*` and `+` registers map to the system clipboard. Plugins like
  neoclip track yank history across sessions.

- **Register events:** `editor.on("register_yanked", handler)` — fired when text is yanked
  into any register.

---

## Summary: Capability Taxonomy

The following table maps capability groups to their key primitives and representative plugins.

| Capability Group | Key Primitives | Representative Plugins |
|---|---|---|
| Text decorations | highlight ranges, virtual text, virtual lines, signs, inlay hints, conceal, codelens | gitsigns, ALE, indent-blankline, nvim-dap, neotest |
| Floating UI | `open_float(pos, size, buffer)`, focus management, z-order | telescope, nvim-cmp, lspsaga, hover, which-key |
| Splits / panels | `split(dir, buffer)`, sidebar, close, resize | neo-tree, toggleterm, diffview, nvim-dap-ui |
| Status bar / winbar | component registration, per-window bars, tabline | lualine, heirline, barbecue, dropbar |
| Completion engine | source registration, CompletionItem, ghost text, trigger | nvim-cmp, blink.cmp, copilot |
| Snippets | tabstop/placeholder/mirror/choice nodes, Tab navigation | LuaSnip, UltiSnips |
| Fuzzy picker | async source, preview pane, multi-select, key bindings | telescope, fzf-lua, mini.pick |
| Buffer manipulation | get/insert/delete/replace, apply_edits, virtual buffer | conform, ALE, oil.nvim, autopairs |
| Keyboard interception | nmap/imap/vmap, fallthrough, operator/motion/textobject registration | surround, leap, autopairs, commentary |
| External processes | async spawn, terminal buffer, file watch, HTTP | ALE, conform, toggleterm, gitsigns, LSP |
| Event system | 20+ named events, wildcard patterns, user events | Every plugin |
| LSP client | server registration, request/response, diagnostics, code actions, semantic tokens | nvim-lspconfig, lspsaga, none-ls |
| Git integration | hunk data, blame, status, root detection | gitsigns, neogit, diffview, fugitive |
| Navigation | jump list, quickfix, location list, cursor teleport, motion registration | telescope, harpoon, leap, ALE |
| Debugging (DAP) | breakpoints, session lifecycle, state inspection, REPL buffer | nvim-dap, nvim-dap-ui |
| Testing | test discovery, async run, result annotations, watch mode | neotest, vim-test |
| Session persistence | save/restore, plugin data hooks, key-value store | vim-obsession, harpoon, projections |
| Syntax / tree-sitter | parse tree access, S-expression queries, injection, folding, indent | nvim-treesitter, indent-blankline, neogen |
| Options system | scoped get/set, option events, custom options, filetype config | vim-sleuth, editorconfig, swenv |
| UI override hooks | cmdline, message area, `ui.input`, `ui.select`, completion popup | noice, which-key |
| Color / themes | highlight group define/query/link, colorscheme change event | lush, nvim-colorizer, mini.colors |
| Macro / repeat | dot-repeat registration, macro recorder integration | vim-repeat, macrothis |
| Clipboard / registers | register read/write, system clipboard, yank events | nvim-neoclip, registers.nvim |

---

## Critical Insights for API Design

**1. Namespace isolation is mandatory.**
Every plugin operates in its own namespace for decorations, highlights, signs, and event
handlers. `buffer.clear_namespace(ns)` must atomically remove all a plugin's decorations
so it can re-render from scratch without flickering or leaving stale state.

**2. Async is the default, not the exception.**
The event loop never blocks. External processes, LSP requests, file I/O, and even completion
source queries are all async. Plugins that block (even for 10ms) will cause perceptible
input lag. The API must make async the path of least resistance.

**3. The completion engine is an extension point, not a feature.**
nvim-cmp's success stems from making sources pluggable (buffer words, LSP, snippets, paths,
git commits, emoji). The editor's completion engine should be a thin pipeline where sources
and sorters are registered by plugins.

**4. The picker is infrastructure, not a feature.**
Telescope became a platform for dozens of plugins because it exposed a composable API.
The built-in picker must be usable by external plugins as their UI layer (file pickers,
symbol pickers, command palettes, diagnostics lists, git log, etc.).

**5. LSP diagnostics and plugin diagnostics share one API.**
The diagnostic system must be source-agnostic: LSP, linters (ALE, nvim-lint), test runners
(neotest), and spell checkers all produce diagnostics. A single `editor.diagnostics` API
with source tags is required; plugins must not each invent their own sign/virtual-text
decoration scheme.

**6. The `apply_edits` batch operation is the fundamental mutation primitive.**
Formatters, LSP code actions, AI assistants, refactoring tools — they all produce lists of
`TextEdit` objects and need to apply them atomically, with the result being a single undoable
action, and without losing extmark positions. This must be a first-class primitive, not
assembled from individual insert/delete calls.

**7. Virtual buffers enable the "filesystem as buffer" pattern.**
oil.nvim's design — edit the filesystem by editing a buffer — requires the editor to support
buffers backed by a plugin, not a file. The custom save handler + virtual document pattern
enables database results, S3 objects, HTTP responses, and test output to all live in normal
editor buffers with the full editing experience.

**8. Plugins need to intercept UI, not just extend it.**
noice.nvim replaces the entire command-line and message area. This is not an edge case — it
is among the most-starred Neovim plugins. The editor must define clean interception points
for its own UI components so they can be replaced without forking the editor.

**9. The session plugin hook system compounds value.**
When every plugin can save/restore state in the session file, the full editor environment —
harpoon marks, DAP breakpoints, telescope history, LSP settings — persists across restarts.
The session API must be extensible from day one.

**10. Operator / motion / text-object registration is the vim grammar extension point.**
Plugins like vim-surround, nvim-treesitter-textobjects, and leap extend Vim's grammar by
registering new operators, motions, and text objects. These must be first-class registration
APIs so the full `[count]["register][operator][count][motion/textobject]` grammar works with
plugin-defined components.
