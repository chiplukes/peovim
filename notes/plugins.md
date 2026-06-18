# Plugin Reference

peovim ships several built-in plugins under `peovim.plugins.*`. None are loaded
automatically — your `init.py` opts in to each one explicitly.

For planned API additions that would enable richer third-party plugins, see
[roadmap.md § Planned Plugin API Extensions](roadmap.md).

---

## Plugin Author API

### Dockable Panels

Both the sidebar and bottom panel accept any object satisfying the panel protocol:

```python
class MyPanel:
    width = 30           # sidebar only: reserved column width

    def render(self, grid: CellGrid) -> None: ...
    def feed_key(self, key: str) -> bool: ...

    # Optional lifecycle hooks (called by PanelHost automatically):
    def on_show(self) -> None: ...
    def on_hide(self) -> None: ...
    def on_focus(self) -> None: ...
    def on_blur(self) -> None: ...
```

Registration and placement:

```python
ui.register_panel("my-panel", MyPanel())              # sidebar (default)
ui.register_panel("my-panel", MyPanel(), host="bottom")  # bottom panel

ui.move_panel("my-panel", "bottom")   # move after registration
ui.show_panel("my-panel")             # show without knowing which host
```

Users can configure placement in `init.py`:

```python
ui.move_panel("explorer", "bottom")
```

The sidebar renders panels with an accordion header list, footer key hints, and a
vertical body. The bottom panel renders a horizontal tab bar. Content is rendered
into a `CellGrid` slice regardless of host; `width` controls horizontal space
reserved in the sidebar only.

### Decoration Types

```python
# peovim/ui/decorations.py

@dataclass class HighlightRegion: ...  # search match, visual selection, word highlight
@dataclass class VirtualText: ...      # appended after line (diagnostics, git blame)
@dataclass class Sign: ...             # gutter char (error dot, git bar, breakpoint)
@dataclass class InlayHint: ...        # inline annotation (LSP type hints, param names)
@dataclass class GhostText: ...        # provisional completion at cursor (copilot)

DecorationSet = list[HighlightRegion | VirtualText | Sign | InlayHint | GhostText]
```

`OverlayChar` (replaces a displayed character temporarily — for jump modes) is
planned but not yet implemented; see [roadmap.md](roadmap.md).

---

## GitHub Copilot (`peovim.plugins.copilot`)

### Files

| File | Purpose |
|---|---|
| `peovim/plugins/copilot.py` | Plugin entry point, event wiring, public API functions |
| `peovim/plugins/copilot_client.py` | Thin `LspClient` wrapper for Copilot-specific protocol |
| `peovim/ui/ghost_text.py` | `GhostTextManager` — suggestion state and decoration output |

### Protocol

Copilot speaks standard LSP JSON-RPC over stdio. Custom methods used:

- `getCompletions` — returns `{ completions: [{ uuid, text, displayText, range, position }] }`
- `signInInitiate` — starts device-flow OAuth, returns `{ userCode, verificationUri }`
- `signInConfirm` — polls auth, returns `{ status: "OK" | ... }`
- `checkStatus` — returns current auth status

`textDocument/didOpen` and `textDocument/didChange` are used for document sync.
The `initialize` call must include `initializationOptions.editorInfo` and
`initializationOptions.editorPluginInfo` or the server rejects all requests.

### Completion Fields

| Field | Meaning |
|---|---|
| `displayText` | What to show as ghost text (may be multi-line) |
| `text` | Full string to insert, starting at `range.start` (may precede cursor) |
| `range` | Buffer range `text` replaces; `range.start.character` may be before cursor col |

On accept, only the suffix of `text` from the current cursor column is inserted
(`text[cursor_col - range.start.character:]`) to avoid re-inserting already-typed chars.

### Binary Lookup

1. `~/.config/peovim/copilot/copilot-language-server[.exe]` (native binary, no Node required)
2. `copilot-language-server` on `PATH` (npm global: `npm install -g @github/copilot-language-server`)

### Configuration (`init.py`)

```python
from peovim.plugins import copilot

copilot.debounce_ms     = 350    # ms to wait after keystroke before requesting (default 350)
copilot.max_ghost_lines = 3      # suggestion lines to display (default 3)
copilot.auto_trigger    = True   # False = manual trigger only (default True)
```

### Keymaps

See [keys.md](keys.md) for the default bindings. Example setup:

```python
from peovim.plugins import copilot

keymap.imap("<A-Tab>", copilot.accept,     desc="Accept Copilot suggestion")
keymap.imap("<A-]>",   copilot.cycle_next, desc="Next Copilot suggestion")
keymap.imap("<A-[>",   copilot.cycle_prev, desc="Previous Copilot suggestion")
keymap.imap("<A-\\>",  copilot.dismiss,    desc="Dismiss Copilot suggestion")
# Only needed when auto_trigger = False:
keymap.imap("<A-Space>", copilot.trigger,  desc="Request Copilot suggestion")
```

### Commands

- `:CopilotAuth` — re-run device-flow authentication
- `:CopilotStatus` — show current auth status in the status bar

---

## Verilog LSP (`peovim.plugins.verilog_lsp`)

Full Verilog IDE integration using the `veriforge` LSP server (`veriforge-lsp`).

### Files

| File | Purpose |
|---|---|
| `peovim/plugins/verilog_lsp/plugin.py` | Entry point: server registration, keymaps, notification handlers |
| `peovim/plugins/verilog_lsp/hierarchy_panel.py` | `VerilogHierarchyPanel` — sidebar tree of module instantiation hierarchy |
| `peovim/plugins/verilog_lsp/signal_trace.py` | Signal connectivity trace picker — tree-style display with directional arrows |

### Server Integration

The plugin registers `veriforge-lsp` as the language server for the `verilog` filetype.
Filetype detection (`.v`, `.sv`, `.vh`, `.svh` → `"verilog"`) is handled by core.

The server provides standard LSP features (hover, go-to-definition, references, symbols)
plus custom Verilog-specific commands via `workspace/executeCommand`.

### Custom LSP Methods

| Method | Direction | Description |
|---|---|---|
| `verilog/hierarchyTree` | server→client notification | Full instantiation tree after each parse |
| `verilog/setTopModule` | execute command | Pin/unpin the top-level module; returns updated tree |
| `verilog/hierarchyGraph` | execute command | Return hierarchy graph, wrapper metadata, and optional visualization payloads |
| `verilog/resolveHierarchyChildren` | execute command | Lazy-load children for a module node |
| `verilog/previewHierarchyBoundaryMove` | execute command | Unified preview for collapse / pull-up / push-down / extract; selects engine via `direction` field. Apply-ready responses open proposed-edit review. |
| `verilog/applyHierarchyBoundaryMove` | execute command | Unified `WorkspaceEdit` apply counterpart; routes to the engine indicated by `direction`. |
| `verilog/traceSignal` | execute command | Return drivers + loads for the signal at cursor (includes `style` and `signalChain`) |
| `verilog.reparse` | execute command | Force full workspace re-parse |

Legacy shim commands (`verilog/previewCollapseHierarchy`, `verilog/applyCollapseHierarchy`,
`verilog/previewHierarchyPullUp`, `verilog/previewHierarchyPushDown`,
`verilog/previewExtractModule`, `verilog/applyExtractModule`) are still registered
and route through the unified handler for backward compatibility.

### Keymaps

See [keys.md](keys.md) for the full Verilog LSP key reference. Default bindings:

| Key | Action |
|-----|--------|
| `<leader>vh` | Toggle hierarchy sidebar panel |
| `<leader>vt` | Trace signal under cursor (opens picker) |
| `<leader>vr` | Trigger full workspace re-parse |
| `<leader>ru` | Preview hier-up for current line or visual selection |
| `<leader>rw` | Preview hier-down (opens cmdline for target module name) |

### Commands

| Command | Action |
|---------|--------|
| `:VerilogHierUp` | Preview hier-up for current line or active visual selection |
| `:VerilogHierDownRange <module_name> [<instance_name>]` | Preview hier-down |
| `:VerilogExtractPreview` | Preview extract-module (legacy alias for hier-down flow) |
| `:VerilogPushDownRange <new_module> [<new_instance>]` | Preview range-based push-down |

### Hierarchy Panel Keys (when panel is focused)

| Key | Action |
|-----|--------|
| `i` | Jump to highlighted instance |
| `d` | Jump to highlighted module definition |
| `s` | Mark highlighted node as refactor source |
| `t` | Mark highlighted node as refactor destination |
| `c` | Preview hier-up for highlighted instance |
| `w` | Preview hier-down into a target module name entered on cmdline |
| `g` | Open wrapper-candidate picker from current hierarchy graph |
| `p` | Pin highlighted module as top |
| `P` | Clear top module pin |
| `Esc` | Close current preview float and clear source/destination marks |

### Refactor UX

Hierarchy keymaps are preview-first: `verilog/previewHierarchyBoundaryMove` opens
a side-by-side proposed-edit review (via `peovim.plugins.proposed_review`). Review
keys are `<leader>ra` to apply, `<leader>rq` to discard, `<leader>rf` to choose
an affected file (multi-file edits), and `]r` / `[r` to navigate review blocks.

Apply-ready collapse, pull-up, and extract responses include proposed-edit review
data. Wrapper nodes show classification badges (`collapse`, `struct`, `behavior`,
`blocked`). Source/destination marks (`[SRC]`, `[DST]`) are visible in the tree.

### Signal Trace Display

The trace picker shows a unified tree. Arrow prefix conveys direction and type;
indentation (5 spaces per level) reflects hierarchy depth.

```
|---> (top.inst filename.v:line#)              load/usage — default color
|<=== (top.inst filename.v:line#)              driver (assign/always) — green
|---<*> (top.inst filename.v:line# a<->b)      port traversal — yellow
|---<=> (top.inst filename.v:line# a<->b)      rename assign — yellow
|---<x> (top.inst filename.v:line#)            unconnected port — red
     |---> (sub.inst filename.v:line#)         nested entry at depth 1
```

### Filetype Guard Pattern

peovim has no buffer-local keymaps; global `nmap` bindings use `_ft_guard(fn)` which
checks `api.active_window().document.filetype == "verilog"` before acting.

### PluginStore Usage

`api.store.get_store("verilog_lsp")` holds:
- `"hierarchy_panel"` — the `VerilogHierarchyPanel` instance (persists across re-opens)

---

## SVN Signs (`peovim.plugins.svnsigns`)

Gutter signs and diff view for Subversion (SVN) working copies. Mirrors the
`gitsigns` feature set: per-line add/change/delete signs derived from `svn diff`,
side-by-side diff view (original from `svn cat` vs. current), and a status sidebar
panel. No write operations are included.

**Requires:** `svn` on `PATH`.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.svnsigns")
```

Default keybindings are registered automatically.

### Keymaps

| Key | Action |
|-----|--------|
| `]h` | Next SVN hunk |
| `[h` | Previous SVN hunk |
| `<leader>sd` | Open side-by-side SVN diff view |
| `<leader>sp` | Preview current hunk in a float |

### SVN Status Panel Keys

| Key | Action |
|-----|--------|
| `<CR>` / `l` | Open diff for M/C entries, open file otherwise |
| `o` | Open the file in a buffer (no diff) |
| `R` | Refresh status |

### Commands

| Command | Action |
|---------|--------|
| `:SvnDiff` | Open side-by-side diff |
| `:SvnStatus` | Toggle SVN status panel |
| `:SvnNextHunk` | Jump to next hunk |
| `:SvnPrevHunk` | Jump to previous hunk |
| `:SvnHunkPreview` | Float-preview current hunk |

---

## File History (`peovim.plugins.filehistory`)

Sidebar panel showing per-file local history snapshots with a live diff preview.
Each entry displays its save timestamp and age. Navigating entries previews a
unified diff against the current buffer; pressing `<CR>` restores the snapshot.

**Requires:** `peovim.plugins.local_history` to be capturing snapshots (reads the
same on-disk store). Can be loaded independently — it reads the store directly.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.local_history")  # captures snapshots
plugins.load("peovim.plugins.filehistory")    # sidebar browser
```

### Keymaps

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>fh` | `FileHistoryToggle` | Toggle file history sidebar |

### Panel-local Keys

| Key | Action |
|-----|--------|
| `<CR>` / `j` / `k` | Navigate entries (preview updates live) |
| `r` / `R` | Refresh history for the current file |
| `q` | Close preview and hide sidebar |

---

## Performance Panel (`peovim.plugins.perf_panel`)

Live performance meter shown as a bottom-panel tab. Displays per-frame timing
(render, maintenance, idle) and FPS as colour-coded bar charts.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.perf_panel")
# Optional: bind a quick-open key
keymap.nmap("<leader>tp", lambda: api.ui.show_bottom_tab("perf"), desc="Perf panel")
```

Or open the bottom panel (`<A-p>`) and switch to the **perf** tab with `>` / `<`.

### Reading the Display

Each bar spans 0–16.7 ms (one 60 fps frame budget):
- **green** — < 25% of budget (< 4.2 ms)
- **yellow** — 25–60% of budget (4.2–10 ms)
- **red** — > 60% of budget (> 10 ms)

Rows: `render` (cell-grid build + flush), `maintnce` (LSP drain, autosave, blink),
`idle` (asyncio sleep time). A healthy 60 fps session shows render + maintnce well
under 5 ms combined and idle around 16 ms.

---

## Proposed Edit Review (`peovim.plugins.proposed_review`)

Presents generated workspace edits (e.g. from LSP code actions or AI suggestions)
as a side-by-side diff: current text on the left, proposed future text on the right.
Provides accept/cancel/next-block/prev-block controls and a file picker for
multi-file edits.

Used by `verilog_lsp` for hierarchy refactoring previews. Can be driven by any
plugin via `api.proposed_review.show(ProposedEditReview(...))`.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.proposed_review")
```

### Keymaps (active while a review is open)

| Key | `<Plug>` | Action |
|-----|---------|--------|
| `<leader>ra` | `ProposedReviewConfirm` | Apply the proposed edit |
| `<leader>rq` | `ProposedReviewCancel` | Discard the proposed edit |
| `<leader>rf` | `ProposedReviewFiles` | Choose affected file (multi-file edits) |
| `]r` | `ProposedReviewNext` | Next change block |
| `[r` | `ProposedReviewPrev` | Previous change block |

### Commands

| Command | Action |
|---------|--------|
| `:ProposedReviewAccept` | Apply the proposed edit |
| `:ProposedReviewCancel` | Discard the proposed edit |
| `:ProposedReviewNext` | Next change block |
| `:ProposedReviewPrev` | Previous change block |
| `:ProposedReviewFiles` | Choose file (multi-file edits) |

---

## Session Additions (`peovim.plugins.session_additions`)

Marks lines added to a buffer since it was first opened in the current session
using a gutter sign (default: `+`). Implemented entirely against the public API.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.session_additions")

# Optional configuration (defaults shown):
options.set("session_additions_enabled", True)
options.set("session_additions_sign_char", "+")
options.set("session_additions_sign_color", "80,200,80")  # RGB or hex
```

No keybindings are registered.

---

## Fquick (`peovim.plugins.fquick`)

Fast file navigation on an `f` prefix. Repurposes the built-in `f{char}` motion to
cycle through recently-opened session files and open workspace file pickers.

**Warning:** this plugin intentionally shadows the `f` motion prefix.

See [keys.md § Plugin: Fquick](keys.md#plugin-fquick-session-file-navigator) for bindings.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.fquick")
```

---

## Codemap (`peovim.plugins.codemap`)

Repo-committed navigation waypoints backed by in-code anchor comments (`cm:XXXXXX`).
A `.codemap.md` file at the project root links human-readable labels to source
anchors; anchors live inside the code as comments so they move with refactors.

See [codemap.md](codemap.md) for the full workflow and `.codemap.md` format.
See [keys.md § Plugin: Codemap](keys.md#plugin-codemap) for bindings.

### Loading (`init.py`)

```python
plugins.load("peovim.plugins.codemap")
```

---

## Out of Scope

| Plugin equivalent | Reason |
|---|---|
| **firenvim** | Requires browser extension + embedded editor — different runtime entirely |
| **lazydev.nvim** | Provides Lua type stubs for Neovim API; Python plugins use Python type hints |
| **plenary.nvim** | Lua utility library; Python plugins use Python stdlib |
