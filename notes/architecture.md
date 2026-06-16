# Architecture Deep Dive

## Layered Architecture

```
┌────────────────────────────────────────────┐
│              Plugin Layer                  │  init.py + user plugins
├────────────────────────────────────────────┤
│              Public API (api.py)           │  Stable interface for plugins
├────────────────────────────────────────────┤
│    Command Layer  │  Config / Options      │  Ex commands, :set, keymaps
├───────────────────┴────────────────────────┤
│              Modal Engine                  │  Mode FSM, key seq buffer,
│    (motions, operators, text objects)      │  operator-motion resolution
├────────────────────────────────────────────┤
│              Editor State                  │  Buffer list, window tree,
│    (buffer, window, workspace, history)    │  tab pages, marks, registers
├────────────────────────────────────────────┤
│         Terminal UI Framework (peovim/ui/) │  WE OWN THIS — no framework
│   • Cell grid renderer + dirty tracking   │  lock-in, no abstraction leaks
│   • Layout engine (split tree → regions)  │  to plugins
│   • Decoration / virtual text system      │
│   • Float / popup system                  │
│   • Key dispatch + event loop             │
├────────────────────────────────────────────┤
│   TerminalBackend (peovim/ui/backend.py)  │  Swappable Protocol.
│   Protocol — read events, write ops,      │  Default: PromptToolkitBackend
│   query size, capability flags            │  Optional: CrosstermBackend
└────────────────────────────────────────────┘
```

**Critical rules:**
- `core/` has zero imports from `ui/` or any terminal library. Fully headless-testable.
- `ui/` imports from `core/` freely, never the reverse.
- Plugins interact only through `api.py`, never with `ui/` internals directly.
- All terminal access goes through `TerminalBackend`. Nothing calls prompt_toolkit directly.

---

## Why We Own the UI Framework

We use prompt_toolkit's I/O layer (via `PromptToolkitBackend`) because it has
excellent cross-platform terminal handling (Win32 Console API on Windows, VT100
on Linux/Mac). We do NOT use its application/widget layer (`Application`,
`UIControl`, `HSplit`, `VSplit`, `KeyBindings`, `FloatContainer`) because:

| Problem | prompt_toolkit full stack | Our own framework |
|---|---|---|
| Rendering performance | Whole-screen invalidation on any change | Per-cell dirty tracking, minimal writes |
| Virtual text (LSP diagnostics, ghost text) | No native concept; hacked into line rendering | First-class `add_virtual_text()` API |
| Plugin UI (file tree, diff view, custom panes) | Plugins need `UIControl` internals — leaky | Plugins call `api.ui.create_panel()` — clean |
| Rich floats (hover docs, reference lists) | `FloatContainer` designed for dropdowns only | Full-featured positioned float system |
| Key dispatch | `KeyBindings` system bypassed anyway for Vim grammar | We own dispatch; backend delivers raw events |
| Multiple cursors | Very hard; hacked into content rendering | `cursor_positions: list[CursorPos]` native |
| Extensibility ceiling | Hit early; UI rewrite required | No ceiling; every future feature slots in |
| Backend lock-in | prompt_toolkit forever | Swap to crossterm without touching anything above |

---

## Terminal Backend

### The Protocol

`peovim/ui/backend.py` defines the only interface the rest of the editor uses to
talk to the terminal. Nothing above this layer imports any terminal library.

```python
from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class TerminalBackend(Protocol):
    """
    Everything the editor needs from the terminal.
    One implementation per backend; the rest of the editor is backend-agnostic.
    """

    # --- Input ---
    def read_events(self) -> AsyncIterator[KeyEvent | MouseEvent]:
        """Async stream of input events. Never blocks the event loop."""
        ...

    # --- Output ---
    def write(self, ops: list[RenderOp]) -> None:
        """
        Apply a list of render operations atomically.
        RenderOp = MoveCursor | PutCell | SetStyle | Clear | ShowCursor | HideCursor
        The backend batches these into the fewest terminal writes possible.
        """
        ...

    def flush(self) -> None:
        """Flush buffered output to the terminal."""
        ...

    # --- Terminal state ---
    def get_size(self) -> tuple[int, int]:
        """Returns (columns, rows). Called on SIGWINCH / resize event."""
        ...

    def enter_raw_mode(self) -> None: ...
    def exit_raw_mode(self) -> None: ...
    def set_mouse_enabled(self, enabled: bool) -> None: ...

    # --- Capability flags ---
    # The modal engine and renderer check these to use better protocols
    # when available, without requiring them.
    def supports_kitty_keyboard(self) -> bool: ...
    def supports_kitty_mouse(self) -> bool: ...     # sub-cell precision
    def supports_true_color(self) -> bool: ...
    def supports_sixel(self) -> bool: ...           # image rendering
    def supports_kitty_graphics(self) -> bool: ...  # image rendering (newer)
    def has_pending_output(self) -> bool: ...
```

### RenderOp — the output primitive

Rather than exposing raw ANSI escape strings, the backend accepts typed
`RenderOp` values. This keeps the rest of the code free of escape sequences
and makes backends easy to test and swap.

```python
@dataclass(frozen=True)
class MoveCursor:
    row: int
    col: int

@dataclass(frozen=True)
class PutCell:
    char: str
    fg: Color
    bg: Color
    attrs: int    # bold | italic | underline | blink | reverse | strikethrough

@dataclass(frozen=True)
class PutCells:
    """Optimised: write a run of same-style cells in one operation."""
    text: str
    fg: Color
    bg: Color
    attrs: int

@dataclass(frozen=True)
class ClearLine:
    row: int

@dataclass(frozen=True)
class ClearScreen: pass

@dataclass(frozen=True)
class ShowCursor: pass

@dataclass(frozen=True)
class HideCursor: pass

@dataclass(frozen=True)
class SetTitle:
    text: str    # terminal window title

RenderOp = MoveCursor | PutCell | PutCells | ClearLine | ClearScreen | ShowCursor | SetCursorStyle | HideCursor | SetTitle
```

`CellGrid.flush()` converts the dirty-cell diff into a `list[RenderOp]` and
passes it to `backend.write()`. The backend translates to ANSI sequences or
Win32 Console calls — the rest of the editor never knows which.

### Backend implementations

| Backend | Module | Dep | Ships | Use case |
|---|---|---|---|---|
| `PromptToolkitBackend` | `peovim/ui/backends/prompt_toolkit.py` | `prompt-toolkit` | Default | All platforms, zero extra build steps |
| `HeadlessBackend` | `peovim/ui/backends/headless.py` | none | Always | Unit tests, CI, scripted use |
| `CrosstermBackend` | `peovim/ui/backends/crossterm.py` | `ed-crossterm` wheel | Optional | Kitty keyboard, full mouse protocols |

#### `PromptToolkitBackend` (default)

Wraps `prompt_toolkit.input.create_input()` and `prompt_toolkit.output.create_output()`.
Translates prompt_toolkit `KeyPress` / `MouseEvent` objects to our `KeyEvent` /
`MouseEvent` types. Translates our `list[RenderOp]` to ANSI escape sequences
via the pt `Output` interface.

Capability flags: `supports_true_color=True`, all others `False` (pt does not
implement Kitty keyboard or Kitty mouse protocols).

#### `HeadlessBackend` (testing)

Accepts a queue of pre-programmed `KeyEvent` / `MouseEvent` to feed as input.
Captures `list[RenderOp]` for assertions. Configurable terminal size.

```python
backend = HeadlessBackend(cols=80, rows=24)
backend.feed_key('<Esc>')
backend.feed_keys('iHello<Esc>')
backend.feed_key(':wq<CR>')

editor = Editor(backend=backend)
await editor.run_until_exit()

assert editor.active_buffer.get_line(0) == 'Hello'
ops = backend.render_ops()
assert any(isinstance(op, PutCells) and op.text == 'Hello' for op in ops)
```

This is how the entire rendering pipeline is tested without a terminal.

#### `CrosstermBackend` (optional)

A thin adapter (`peovim/ui/backends/crossterm.py`) wrapping the `ed_crossterm`
PyO3 extension. Installed as an optional separate wheel alongside the Python package.

Key advantages over `PromptToolkitBackend`:
- **Kitty keyboard protocol**: disambiguates `<C-m>` vs `<CR>`, `<C-i>` vs
  `<Tab>`, `<Esc>` vs `<A-*>`, modifier+function-key combos. Eliminates a
  class of Vim keybinding edge cases that require hacks under VT100.
- **SGR mouse protocol**: precise mouse coordinates, button release events,
  modifier keys during mouse events.
- **Better Windows support**: ConPTY-aware, no VT100 fallback quirks.

Opt-in via `init.py`:
```python
editor.backend = 'crossterm'   # falls back to prompt_toolkit if not installed
```

Or via environment variable: `ED_BACKEND=crossterm`

### Kitty keyboard protocol and Vim keybindings

Under standard VT100, several key combinations are ambiguous or unrepresentable:

| Key press | VT100 sends | Ambiguous with |
|---|---|---|
| `Ctrl+m` | `\r` (0x0D) | `Enter` |
| `Ctrl+i` | `\t` (0x09) | `Tab` |
| `Ctrl+[` | `\x1b` (0x1B) | `Escape` |
| `Shift+Enter` | `\r` | `Enter` (indistinguishable) |
| `Ctrl+Shift+f` | depends on terminal | often same as `Ctrl+f` |

Vim has worked around these for decades with heuristics and timeouts (see
`timeoutlen`). The Kitty keyboard protocol sends unambiguous sequences for all
keys including modifiers. When `CrosstermBackend` is active and the terminal
supports it:
- `<C-m>` and `<CR>` are distinct `KeyEvent` values
- `<C-i>` and `<Tab>` are distinct
- `<C-[>` and `<Esc>` are distinct
- `<S-CR>`, `<C-S-f>` etc. all work without heuristics

The modal engine checks `backend.supports_kitty_keyboard()` at startup and
adjusts its key normalisation accordingly. The difference is transparent to
plugins and user keymaps.

### Backend selection and fallback

```python
# peovim/ui/backend_factory.py
def create_backend(requested: str | None = None) -> TerminalBackend:
    name = requested or os.environ.get('ED_BACKEND', 'prompt_toolkit')
    match name:
        case 'crossterm':
            try:
                from peovim.ui.backends.crossterm import CrosstermBackend
                return CrosstermBackend()
            except ImportError:
                warnings.warn('crossterm backend not installed, falling back to prompt_toolkit')
                return PromptToolkitBackend()
        case 'headless':
            return HeadlessBackend()
        case _:
            return PromptToolkitBackend()
```

---

## Data Flow

```
Terminal input event (key / mouse)
        │  prompt_toolkit create_input()
        ▼
EventLoop.run()  [peovim/ui/event_loop.py]
        │  KeyEvent / MouseEvent dataclass
        ▼
Modal Engine  [peovim/modal/engine.py]
        │  buffers sequence: [count]["reg][op][count][motion/obj]
        │  resolves complete command
        ▼
Action dataclass  [peovim/modal/actions.py]
        │
        ▼
ActionDispatcher  [peovim/modal/dispatcher.py]
        │
        ├──► Buffer mutation → PieceTable → Edit record → UndoStack
        ├──► Cursor / Window update
        ├──► Mode transition
        ├──► Ex command execution
        └──► Plugin event hooks (on_key, on_buffer_change, ...)
                        │
                        ▼ (async, background)
                   Syntax engine / LSP client

State change → mark regions dirty
        │
        ▼
render_window()  [peovim/ui/window_renderer.py]
        │  layout pass: split tree → Rect per window
        │  per-window: visible lines → cells (syntax spans + all decoration types)
        │  diff against previous CellGrid
        ▼
Terminal output (minimal escape sequences)
        │  prompt_toolkit create_output()
        ▼
Terminal
```

---

## Hot Code Paths

Every keystroke triggers this chain. Latency budget for a responsive editor is ~8ms
(target 120fps; 16ms at 60fps). Each step is profiled separately.

```
KeyEvent received
  │
  ├─ Modal engine key parse          ~1–10 µs    pure Python FSM, negligible
  ├─ PieceTable insert/delete        ~0.13 ms on 5 000-line file — incremental
  │                                  line-index splice + piece coalescing done;
  │                                  _find_piece uses bisect_right on _piece_offsets
  ├─ Cursor / window update          ~1 µs       arithmetic
  ├─ tree-sitter parse               incremental via tree.edit() + old_tree cache;
  │                                  query restricted to visible range + 20-line pad;
  │                                  C extension, off main thread
  ├─ Highlight query (visible range) ~500–5000 µs C extension; submit() accepts
  │                                  visible_end_line to restrict the byte range
  ├─ render_window() (lines → cells) ~2–15 ms    *** PRIMARY BOTTLENECK — pure Python loop ***
  ├─ CellGrid diff                   ~300–800 µs pure Python tuple comparison
  └─ Terminal write                  ~200–500 µs buffered I/O
```

**`render_window()` is the dominant cost.** For a 50-line × 120-col visible area that is
6000 character iterations with style lookups per frame. In CPython this takes 3–15ms
depending on decoration density. A Cython/native extension (`peovim._native.window_renderer`)
can replace the inner loop when compiled.

**tree-sitter** is the second concern for large files or complex grammars. It already
runs in C and releases the GIL, making it the natural candidate for background threading.

Everything else (modal engine, cursor math, PieceTable ops for typical edits) is
comfortably sub-millisecond.

### Render hot-path optimizations (currently in place)

- **Cell tuple interning** — `_CELL_CACHE: dict[(fg,bg,attrs), {char: cell}]` in
  `_cell_grid_pure.py`. After the first render, all `(char, style)` combinations are
  cached; subsequent frames reuse the same tuple objects, eliminating GC pressure
  from ~1500 newly-tracked tuples per warm render frame.
- **Window `CellGrid` reuse** — `WindowRenderController` caches one `CellGrid` per
  window in a `WeakKeyDictionary`. On each render pass, the cached grid is cleared and
  passed to `render_window()` instead of allocating a new one.
- **Persistent syntax style cache** — `Theme._style_cache` stores the token-group →
  `Style` mapping across frames instead of rebuilding it from scratch each frame.
- **Dirty row tracking** — `CellGrid.flush()` tracks dirty rows so diff work scales
  with rows touched, not total grid size.
- **Reusable internal lists** — `flush()` reuses `self._ops` and `self._run_chars`
  lists across frames rather than allocating new ones each call.
- **Decoration pre-indexing** — `render_window()` indexes visible decorations by line
  before entering the row loop (O(n) once → O(1) per row).
- **Split-tree layout caching** — layout is skipped when the split-tree signature and
  viewport dimensions are unchanged between frames.
- **Frame theme resolved once** — `Theme` is resolved once per frame and reused across
  all window and sidebar renders.

### Performance measurement

Run the profiling harness before and after any renderer, syntax, or persistence
change to get a ranked baseline:

```
uv run python -m peovim.debug.profile_workloads --scale 0.5 --repeat 3
```

Use `--repeat 3` or higher — single runs are noisy.

**Current baseline** (scale 0.5, repeat 3):

| Workload | Mean cost |
|---|---|
| Full frame composition (`frame_render`) | ~2.1 ms |
| Atomic project-local persistence | ~2.1 ms |
| Compare/diff side-by-side rendering | ~0.9 ms |
| LSP-heavy decoration rendering | < 0.9 ms |
| Picker/UI rendering | < 0.9 ms |

---

## Concurrency Model

### Thread ownership rules

These rules are **absolute**. Violating them is a bug.

```
Main thread owns:
  • All mutable editor state (Buffer, Window, Workspace, Registers, Marks)
  • Modal engine state machine
  • asyncio event loop
  • Terminal input / output

Background threads / executors receive only:
  • BufferSnapshot  — immutable, created on main thread, passed by value
  • WindowSnapshot  — immutable (scroll offset, cursor pos, rect, options)
  • Immutable configuration (Theme, options dict copy)

Background threads NEVER:
  • Read live Buffer, Window, or Workspace objects
  • Write to any editor state
  • Call into the modal engine
  • Touch the terminal output
```

Results from background threads are posted back to the main thread via
`asyncio.get_event_loop().call_soon_threadsafe(callback, result)`.
The main thread applies results (e.g. updated syntax spans) at the next render frame.

### BufferSnapshot — the isolation primitive

```python
@dataclass(frozen=True)
class BufferSnapshot:
    """
    Immutable point-in-time view of a buffer. Safe to pass to any thread.
    Created cheaply: piece list is small, original/add buffers are bytes (immutable).
    """
    pieces:        tuple    # tuple[Piece, ...] — immutable copy of piece list
    original:      bytes    # bytes = immutable in Python
    add:           bytes    # bytes() of bytearray — O(n) copy, done once per edit
    version:       int      # monotonically increasing; stale results are discarded
    line_offsets:  tuple    # tuple[int, ...] — byte offsets of line starts
    filetype:      str      # e.g. 'python', 'rust', '' = unknown
    pending_edits: tuple    # tuple of tree-sitter InputEdit for incremental parse
```

`WindowSnapshot` (`peovim/core/snapshot.py`) is similarly a frozen dataclass
containing scroll offset (`scroll_line`, `scroll_col`), cursor position, window
dimensions (`width: int`, `height: int` — not a `Rect`), local options, and closed
folds — everything `render_window()` needs, nothing mutable.

### Concurrency architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Main Thread — asyncio event loop                           │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Input coro   │  │ Render coro  │  │ LSP I/O coros    │  │
│  │              │  │ (120fps cap) │  │ (async read/write│  │
│  │ KeyEvent →   │  │              │  │  to LSP process) │  │
│  │ modal engine │  │ uses cached  │  │                  │  │
│  │ → Action →   │  │ syntax spans │  │ results posted   │  │
│  │ state change │  │ from last    │  │ via              │  │
│  │ → snapshot() │  │ background   │  │ call_soon_       │  │
│  │              │  │ result       │  │ threadsafe()     │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│          │                │                                  │
│          │ snapshot       │ post result                      │
└──────────┼────────────────┼──────────────────────────────────┘
           │                │
           ▼                ▲
┌──────────────────────────────────────────────────────────────┐
│  ThreadPoolExecutor  (workers, no shared mutable state)      │
│                                                              │
│  Syntax worker               Window render workers           │
│  • receives BufferSnapshot   • receive WindowSnapshot        │
│  • tree-sitter parse/query   • produce per-window CellGrid   │
│  • returns list[Span]        • merged by main thread         │
│  • discards if version stale • discards if version stale     │
└──────────────────────────────────────────────────────────────┘
```

The architecture makes **no assumption about GIL presence**. Thread safety is
enforced explicitly via ownership rules and immutable snapshots — not implicitly
via the GIL. The codebase works correctly under standard CPython and gains
additional parallelism under free-threaded Python builds.

### Thread safety checklist for new code

When adding any new feature, verify:
- [ ] Does it mutate `Buffer`, `Window`, `Workspace`, or any editor state? → Main thread only.
- [ ] Does it read editor state from a background thread? → Must use a snapshot.
- [ ] Does it post results back to the main thread? → Use `call_soon_threadsafe`.
- [ ] Is the data passed to a background thread immutable (`frozen=True`, `tuple`, `bytes`)? → Yes.
- [ ] Does it assume the GIL for correctness? → Fix it. GIL must not be load-bearing.

---

## Terminal UI Framework Components

### Unified panel system

The editor has two dockable panel hosts — a vertical left sidebar and a horizontal
bottom panel — both inheriting from a shared `PanelHost` base class.

#### `PanelHost` base class (`peovim/ui/panel_host.py`)

`PanelHost` owns all shared tab-management, focus, and key-routing logic:

- Tab registry: `_tabs` dict + `_tab_order` list, `register_tab / get_tab / unregister_tab / list_tabs`
- Active tab tracking: `_active_tab`, `active_tab` / `active_tab_name` properties
- Visibility/focus state: `_visible`, `_focused`, `show_tab / show_active_tab / hide / toggle / focus / blur`
- Tab cycling: `next_tab / prev_tab` (subclasses override to exclude internal tabs)
- Lifecycle hooks: `on_show / on_hide / on_focus / on_blur` called automatically on content objects via `_call_hook(content, hook_name)`
- Key routing: `feed_key` dispatches `_key_to_plug` first, then delegates to the active tab
- Plug execution: `_execute_plug` uses `_binding_registry` if set, otherwise falls back to `_builtin_plugs()` dict (overridden by each subclass)
- `preferred_host` metadata per tab: `_preferred_host: dict[str, str]` stores "sidebar" or "bottom" for each registered name

Content objects only need `render(grid: CellGrid) -> None` and `feed_key(key: str) -> bool`. Optional attributes (`title`, `width`, `on_show`, `on_hide`, `on_focus`, `on_blur`) are queried with `getattr` defaults, so the same content works in either host.

#### Persistent sidebar host

`peovim/ui/sidebar.py` extends `PanelHost` for the left sidebar.

Key design points:

- The sidebar is a layout-reserved UI region, not a `Workspace` window.
- When visible, the event loop reserves left-side width before calling
    `compute_layout()` for editor windows.
- Editor windows render in the remaining width, so split behavior remains owned
    by `Workspace` and `ui.layout`.
- Sidebar focus is a UI-level concern separate from which editor window is the
    active split.

`SidebarHost` adds: `_width`, `_style` (SidebarStyle), `blink_on`, multi-section rendering (accordion headers + body + footer), `cursor_row()` for terminal cursor positioning, and `reserved_width()`.

- `SidebarPanel` is a small rendering/input protocol that plugins can satisfy.
- `TreeSidebarPanel` adapts the existing `TreeView` widget into the host.

Backward-compat aliases (`active_panel`, `active_panel_name`, `register_panel`, `list_panels`, `show_panel`, `toggle_panel`, etc.) delegate to the base class.

Plugins interact with the host through `UIAPI` rather than touching event-loop
internals directly. The current public methods include:

- `register_sidebar_panel(name, panel)`
- `show_sidebar_panel(name, panel, focus=True)`
- `toggle_sidebar_panel(name, panel=None, focus=True)`
- `list_sidebar_panels()`
- `next_sidebar_panel()` / `prev_sidebar_panel()`
- `show_tree_sidebar(...)` as a convenience for tree-backed panels

The current built-in sidebar panels are explorer, git status, outline, references, and workspace symbols.

#### Focus and key routing

Sidebar navigation keys are registered as `<Plug>` mappings in `EditorAPI._register_sidebar_plugs()` (called during `EditorAPI.__init__`):

| Plug | Default | Action |
|------|---------|--------|
| `<Plug>SidebarFocusLeft`  | `<A-h>` | Focus sidebar (or `SmartFocusWindow("h")` when sidebar hidden) |
| `<Plug>SidebarFocusRight` | `<A-l>` | Return focus to editor (or `SmartFocusWindow("l")`) |
| `<Plug>SidebarNextPanel`  | `<A-j>` | Cycle to next panel (no-op when sidebar not focused) |
| `<Plug>SidebarPrevPanel`  | `<A-k>` | Cycle to previous panel (no-op when sidebar not focused) |

Users can rebind any of these via `keymap.nmap(key, "<Plug>SidebarFocusLeft")` etc.

Key dispatch is handled in `presentation_controller.handle_sidebar_navigation_key`, which runs **before** the modal engine. It calls `BindingRegistry.find_keys_for_plug()` to resolve the current binding dynamically, so remapped keys are honoured automatically. `SidebarHost._get_footer_lines()` uses the same lookup to display live bindings in the panel footer.

When the sidebar is visible but not focused, only `SidebarFocusLeft` is checked; other nav keys pass through to the engine. When the sidebar is focused, `SidebarFocusRight/NextPanel/PrevPanel` are checked first, then unmatched keys are forwarded to the active panel's `feed_key`.

### Bottom panel

The editor has a persistent bottom panel (`peovim/ui/bottom_panel.py`) that mirrors the sidebar pattern but is oriented horizontally along the bottom of the screen (above the status bar).

#### Layout

When the panel is visible, `BottomPanelHost.reserved_height(total_rows)` returns the number of rows to reserve.  `FrameController.compute_frame_layout` reduces `win_rows` accordingly and returns a `bottom_panel_rect: Rect | None` as the fifth element of its result tuple.  The panel is rendered by `_render_bottom_panel` after the sidebar but before the status bar.

Which-key's own reserved-height slot is suppressed when the bottom panel is visible (it renders inside the panel's hidden "keys" tab instead).

#### Tab model

`BottomPanelHost` inherits all tab-management, focus, and lifecycle-hook machinery from `PanelHost`.  It adds `_height`, `_pre_wk_tab` (which-key state), orientation-specific rendering (horizontal tab bar + separator + body), and height sizing.

Content protocol: `render(grid)`, `feed_key(key) -> bool`.  Optional `title: str` is shown in the tab bar (defaults to registration name).

The tab bar (row 0) shows all tabs except the hidden "keys" tab.  A separator occupies row 1.  The active tab body fills the remaining rows.  Mouse clicks on the tab bar switch tabs.

#### Built-in tabs

| Name | Class | Purpose |
|------|-------|---------|
| `"output"` | `LogOutputTab` | Captures Python `logging` output in real time |
| `"keys"` | `WhichKeyTab` | Renders which-key hints; activated programmatically, hidden from tab bar |

#### Focus and key routing

Internal keys (active only while bottom panel is focused) live in `BottomPanelHost._key_to_plug` — a key → plug-name dict separate from the global nmap system.  Defaults:

| Key | Plug | Action |
|-----|------|--------|
| `<Esc>` | `BottomPanelBlur` | Blur panel (keep visible) |
| `q` | `BottomPanelClose` | Hide panel |
| `[` / `]` | `BottomPanelShrink` / `BottomPanelGrow` | Resize |
| `<` / `>` | `BottomPanelPrevTab` / `BottomPanelNextTab` | Cycle tabs |

Overlay-level plugs (registered in `EditorAPI._register_sidebar_plugs`):

| Plug | Default | Action |
|------|---------|--------|
| `<Plug>BottomPanelToggle` | `<A-p>` | Toggle show/hide |
| `<Plug>BottomPanelFocus` | — | Focus the panel |
| `<Plug>BottomPanelBlur` | — | Blur the panel |
| `<Plug>BottomPanelNextTab` | — | Next tab |
| `<Plug>BottomPanelPrevTab` | — | Prev tab |

#### Which-key integration

When `UIAPI.show_which_key()` is called and the bottom panel is visible:
1. Save the current active tab as `_pre_wk_tab`
2. Switch `_active_tab` to `"keys"`
3. The `WhichKeyTab` renders the `WhichKeyPanel` content at row 0 of the body

When `hide_which_key()` is called, `_pre_wk_tab` is restored.

If the bottom panel is hidden, which-key falls back to its original above-status-bar rendering.

#### Public API (`UIAPI`)

```python
register_bottom_tab(name, tab)
show_bottom_tab(name, tab=None, *, focus=True)
toggle_bottom_panel(*, focus=True) -> bool
hide_bottom_panel()
focus_bottom_panel() / blur_bottom_panel()
is_bottom_panel_visible(tab_name=None) -> bool
get_bottom_tab(name) -> tab | None
list_bottom_tabs() -> list[str]
bottom_nmap(key, plug_name)
api.ui.log_output   # LogOutputTab instance
```

### `peovim/ui/cell_grid.py` — Cell buffer with dirty tracking

The fundamental rendering primitive. Owns a 2D grid of styled characters.
Only changed cells are written to the terminal on each frame.

`cell_grid.py` dispatches to a compiled native extension (`peovim._native.cell_grid`)
when available, falling back to the pure-Python implementation in `_cell_grid_pure.py`.
All callers import only `CellGrid` from this module.

Cells are plain tuples `(char, fg, bg, attrs)` stored in a 2D list. A module-level
`_CELL_CACHE` interns tuples by `(fg, bg, attrs)` + char so repeated writes reuse
the same object instead of allocating new tuples each frame.

```python
class CellGrid:
    width: int
    height: int
    _current: list[list[Cell]]    # current frame
    _prev:    list[list[Cell]]    # last flushed state (double-buffer)
    _dirty_rows: set[int]         # rows modified since last flush

    def write(self, row, col, char, fg=None, bg=None, attrs=0) -> None:
        """Write a single character cell."""

    def write_str(self, row, col, text, fg=None, bg=None, attrs=0) -> None:
        """Write a string of cells starting at (row, col). Clips at width."""

    def write_padded(self, row, col, text, width, fg=None, bg=None, attrs=0) -> None:
        """Write text then fill remaining span with spaces using the same style."""

    def fill(self, row, col, width, char=" ", fg=None, bg=None, attrs=0) -> None:
        """Fill width cells with a uniform character and style."""

    def paint_style_range(self, row, col_start, col_end, fg, bg, attrs) -> None:
        """Re-style an existing cell range without changing characters."""

    def blit(self, src: CellGrid, dest_x, dest_y) -> None:
        """Copy src onto this grid at (dest_x, dest_y)."""

    def clear(self) -> None:
        """Reset all cells to blank without invalidating _prev."""

    def flush(self) -> list[RenderOp]:
        """Diff _current against _prev; return list[RenderOp] for changed rows only. Caller writes ops to the backend."""
```

### `peovim/ui/layout.py` — Split tree → screen regions

A pure function: takes the `Workspace` split tree and terminal dimensions,
returns a `dict[WindowLeaf, Rect]` mapping each window to its screen region.

```python
@dataclass(frozen=True)
class Rect:
    x: int; y: int; width: int; height: int

def compute_layout(root: SplitNode, total: Rect) -> tuple[dict[WindowLeaf, Rect], list[Rect]]:
    # Second return value is a list of separator Rects (divider lines).
    match root:
        case WindowLeaf() as leaf:
            return {leaf: rect}
        case HSplitNode(top, bottom, ratio):
            top_h = max(1, int(rect.height * ratio))
            bot_h = rect.height - top_h - 1  # -1 for divider line
            return (
                compute_layout(top,    Rect(rect.x, rect.y,             rect.width, top_h)) |
                compute_layout(bottom, Rect(rect.x, rect.y + top_h + 1, rect.width, bot_h))
            )
        case VSplitNode(left, right, ratio):
            left_w = max(1, int(rect.width * ratio))
            right_w = rect.width - left_w - 1  # -1 for divider
            return (
                compute_layout(left,  Rect(rect.x,           rect.y, left_w,  rect.height)) |
                compute_layout(right, Rect(rect.x + left_w + 1, rect.y, right_w, rect.height))
            )
```

Split dividers are drawn directly into the cell grid (vertical `│`, horizontal `─`).
The active window's dividers get a distinct style.

### `peovim/ui/window_renderer.py` — `render_window()`

`render_window()` is a module-level function (not a class). `window_renderer.py`
is a shim that dispatches to the compiled native extension when available
(`peovim._native.window_renderer`), falling back to the pure-Python implementation
in `_window_renderer_pure.py`.

```python
def render_window(
    snapshot: WindowSnapshot,
    rect: Rect,
    is_active: bool,
    decorations: DecorationSet | None = None,
    highlight_spans: list[HighlightSpan] | None = None,
    theme: Theme | None = None,
    extra_decorations: list | None = None,
    grid: CellGrid | None = None,
    sign_registry: object | None = None,
) -> CellGrid:
```

Given a `WindowSnapshot` and its `Rect`, fills a `CellGrid` with:
1. **Gutter** — line numbers, signs (errors, breakpoints, git change markers)
2. **Text content** — visible lines, with syntax highlighting and text layout (tabs, soft wrap, conceals)
3. **Decorations** — highlight regions, overlay chars, indent guides, colour column
4. **Virtual lines** — full virtual rows (code lens, diff lines) inserted after their anchor
5. **Cursor** — painted on top of everything else

If `grid` is provided and matches rect dimensions, it is cleared and reused.
The caller blits the returned grid into the master grid at `(rect.x, rect.y)`.

`render_window()` takes only immutable inputs and returns a `CellGrid`. It has
no side effects, making it safe to call from background threads.

### `peovim/ui/decorations.py` — Decoration / virtual text system

Decorations are overlaid on top of base text rendering. All are first-class
concepts, not hacks into line rendering.

```python
@dataclass(frozen=True)
class HighlightRegion:
    """Overlay color range on buffer text (syntax, search match, etc.)."""
    start_line: int
    start_col: int    # character col (inclusive)
    end_line: int
    end_col: int      # character col (exclusive)
    style: Style
    priority: int = 0
    kind: str = "highlight"

@dataclass(frozen=True)
class VirtualText:
    """Inline virtual text appended after a buffer line."""
    line: int
    text: str
    style: Style
    priority: int = 0

@dataclass(frozen=True)
class VirtualLine:
    """One or more blank virtual lines inserted after a buffer line."""
    after_line: int  # -1 = before line 0
    style: Style
    count: int = 1

@dataclass(frozen=True)
class Sign:
    """Single-character marker displayed in the gutter."""
    line: int
    char: str
    style: Style
    priority: int = 0
    kind: str = "sign"

@dataclass(frozen=True)
class InlayHint:
    """Inline hint inserted before a column (type annotations, param names)."""
    line: int
    col: int
    text: str
    style: Style

@dataclass(frozen=True)
class GhostText:
    """Faded suggested text overlaid on the buffer (LSP completion ghost)."""
    line: int
    col: int
    text: str
    style: Style

@dataclass(frozen=True)
class OverlayChar:
    """Replace a single cell's visual character without changing the buffer."""
    line: int
    col: int
    display_char: str
    style: Style

@dataclass(frozen=True)
class CodeLens:
    """Actionable annotation line above a buffer line."""
    line: int
    text: str
    style: Style

@dataclass(frozen=True)
class Conceal:
    """Hide or replace a range of text (folding, markdown, etc.)."""
    line: int
    start_col: int
    end_col: int
    replacement: str   # "" to hide, or a single-char substitute

DecorationSet = list
```

Each Window maintains its own `DecorationSet`. Plugins add decorations via
`api.window.add_decoration(...)`. They're cleared and rebuilt on each render
frame (cheap — it's just a list rebuild from fast sources like LSP state).

### `peovim/ui/float_manager.py` — Floating windows

Floats render on top of the cell grid after the main layout pass.
They have their own mini cell grids that are composited at render time.

```python
class FloatManager:
    _floats: list[Float]
    _focused: FloatHandle | None

    def open_float(
        self,
        content: str | list[FloatLine],
        *,
        anchor: FloatAnchor | None = None,
        width: int = 60,
        height: int = 10,
        border: bool = True,
        title: str = "",
        focusable: bool = False,
        z_order: int = 0,
        on_close: Any = None,
        on_key: Any = None,
    ) -> FloatHandle: ...

    def close_float(self, handle: FloatHandle) -> None: ...
    def focus(self, handle: FloatHandle) -> None: ...
    def close_all(self) -> None: ...
```

`open_float()` returns a `FloatHandle`. Callers update or close the float through
the handle:

```python
handle.set_content(lines)   # update content in-place
handle.set_title(title)
handle.set_anchor(anchor)
handle.set_size(width, height)
handle.close()
handle.is_open              # property
```

`FloatLine = str | list[tuple[str, Style]]` — each line can be a plain string or
a list of `(text, Style)` segments for syntax-highlighted content.

Anchor types: `Centered()`, `Absolute(x, y)`, `CursorRelative(row_offset, col_offset)`.

Floats used for: completion menu, hover docs, diagnostic detail, file picker,
`:` command line history popup, codemap/references preview.

### `peovim/ui/event_loop.py` — Main loop

`EventLoop` is the asyncio main loop. It wires together a set of focused
sub-controllers rather than implementing everything inline:

- **`InputController`** — reads raw events from the backend, normalises keys,
  feeds the modal engine
- **`OverlayPresentationController`** — intercepts keys before the modal engine
  for floats, picker, command line, sidebar, and completion widgets
- **`FrameController`** — computes per-frame layout (sidebar width, bottom panel
  height, window rects, status bar)
- **`WindowRenderController`** — manages per-window `CellGrid` reuse, submits
  `render_window()` jobs to the executor, blits results into the master grid
- **`RenderCycleController`** — tracks dirty state and caps frame rate at `_fps`
  (default 120); wakes early on `invalidate()` via `asyncio.Event`
- **`RenderJobExecutor`** — dispatches window render jobs to `ThreadPoolExecutor`
- **`LspUiAdapter`** — bridges LSP results (diagnostics, completions, hover) into
  decorations and floats
- **`CommandLineController`** — handles `:` command-line mode input and completion
- **`MouseDispatcher`** — maps terminal mouse events to editor actions and widget
  interactions

The two executors are:
- `self._executor` — syntax parse jobs (tree-sitter, releases GIL)
- `self._render_executor` — window render jobs

Background results are posted to the main thread via `call_soon_threadsafe()`.

---

## Key Dispatch

We do NOT use prompt_toolkit's `KeyBindings`. We receive raw `KeyPress` events
from `create_input()` and dispatch them ourselves. This gives us full control
over the Vim grammar state machine with no framework interference.

```python
# prompt_toolkit delivers:
#   KeyPress(key=Keys.ControlC, data='\x03')
#   KeyPress(key='a', data='a')
#   MouseEvent(position=Point(x=10,y=5), event_type=MouseEventType.MOUSE_DOWN, ...)

# We convert to our types:
@dataclass
class KeyEvent:
    key: str          # 'a', '<C-c>', '<Esc>', '<CR>', '<F5>', '<S-Tab>', etc.
    raw: str          # original data

@dataclass
class MouseEvent:
    x: int; y: int
    kind: MouseKind   # PRESS, RELEASE, MOVE, SCROLL_UP, SCROLL_DOWN
    button: int       # 1=left, 2=middle, 3=right
    modifiers: int    # CTRL | SHIFT | ALT bitmask
```

The modal engine receives `KeyEvent` and manages all Vim grammar parsing
internally. The event loop is just a delivery mechanism.

### Engine context provider

`ModalEngine` stores shadow copies of cursor, line count, scroll line, and
document (`_cursor`, `_line_count`, `_scroll_line`, `_document`).  At the
start of every `feed_key()` call, `_sync_from_provider()` pulls fresh state
from a `_provider` callable (set in `main.py` as `lambda: workspace.active_window`).
This guarantees the engine always sees the current window state on every
keypress, eliminating the "first keypress resolves from stale position" bug.

The legacy `set_cursor` / `set_line_count` / `set_scroll` / `set_document`
setters remain for backward compatibility (test harnesses that don't wire a
provider still use them; they are overridden by the provider on the next
`feed_key` in production).

---

## Piece Table

Text is stored in two append-only buffers:
- **original**: immutable, the initial file content loaded from disk
- **add**: append-only, all inserted text accumulates here

The piece table is a sequence of spans referencing these buffers:
```python
@dataclass(slots=True)
class Piece:
    buf: Literal['original', 'add']
    start: int      # byte offset into buffer
    length: int     # byte length
    newlines: int   # cached \n count for fast line-index math

class PieceTable:
    original: bytes
    add: bytearray
    pieces: list[Piece]

# PieceTable lives in peovim/core/buffer.py (same module as Buffer).
```

**Insert** at byte offset `pos`: split the piece containing `pos` into two,
insert a new piece referencing the new text in the add buffer.

**Delete** of `length` bytes at `pos`: split pieces at `pos` and `pos+length`,
drop the middle piece(s).

Each operation produces an `Edit` record used for undo. Undo restores a prior
piece list snapshot (pieces are small — snapshots are cheap).

**Line index**: `line_offsets` — a `tuple[int, ...]` of byte offsets for the
start of each line, stored in `BufferSnapshot`. Rebuilt lazily after buffer
mutations.

---

## Modal Engine: Key Sequence Parsing

Vim's normal mode command grammar:
```
cmd    ::= [count] ['"' reg] op [count] (motion | obj)
         | [count] ['"' reg] single
         | [count] motion

count  ::= [1-9][0-9]*
reg    ::= [a-zA-Z0-9"*+_./%#:=-]
op     ::= d | y | c | > | < | = | g~ | gu | gU | ! | gq | ...
motion ::= h|j|k|l | w|W|b|B|e|E | 0|^|$|g_ | f{c}|F{c}|t{c}|T{c} | ...
obj    ::= [ia] [w|W|s|p|"|'|`|(|{|[|<|t]
single ::= x|X|s|S|D|C|p|P|J|u|. | ZZ|ZQ | ...
```

State machine states:
```
IDLE → COUNT1 → REGISTER → OPERATOR → COUNT2 → MOTION/OBJECT
                                               → FIND_CHAR (after f/F/t/T)
                                               → MARK_CHAR  (after m/`/')
                                               → MACRO_CHAR (after q/@)
                    ↓ (no operator)
              SINGLE_CMD / MOTION
```

Each resolved command emits an `Action` dataclass. The state machine never
calls editor functions directly — it only produces actions. This makes
macro recording and dot-repeat trivial: just replay the action stream.

---

## Undo System

Every buffer mutation produces an `Edit`:
```python
@dataclass
class Edit:
    kind: Literal['insert', 'delete']
    pos: int      # byte offset at time of edit
    text: bytes   # bytes inserted or deleted
```

Edits record cursor position via `_change_site(edit.pos)`; `document.undo()` /
`document.redo()` return `(char_line, char_col)` and the dispatcher moves the
cursor to the change site after each undo/redo.

Edits are grouped into **transactions** (undo units):
- Continuous typing in Insert mode = one transaction
- Each operator (`d`/`c`/`y`/`>`...) = one transaction
- A transaction boundary is always created on mode entry

`u` rolls back one transaction; `Ctrl-r` reapplies it.
The transaction boundary on mode change means `u` in Normal mode always
undoes the entire last Insert session, matching Vim's behavior exactly.

---

## Split Tree

```python
@dataclass
class HSplitNode:
    top:    SplitNode
    bottom: SplitNode
    ratio:  float      # 0.0–1.0, fraction of height for top

@dataclass
class VSplitNode:
    left:  SplitNode
    right: SplitNode
    ratio: float       # fraction of width for left

@dataclass
class WindowLeaf:
    window: Window

SplitNode = HSplitNode | VSplitNode | WindowLeaf
```

**Split a leaf**: replace `WindowLeaf` in the tree with an `HSplitNode(old, new, 0.5)`.
**Close a leaf**: replace the leaf's parent with its sibling.
**Resize**: update `ratio` on the parent node.
**Focus `h/j/k/l`**: traverse the tree to find the geometrically adjacent leaf.

The layout pass (`compute_layout`) is a pure function over this tree — no
mutable state, trivially testable without a terminal.

---

## Buffer / Window / Workspace Model

```
Editor
├── BufferList                     global, shared across tabs
│   └── Buffer (0..n)
│       ├── PieceTable             text storage
│       ├── path: str | None
│       ├── encoding: str
│       ├── dirty: bool
│       ├── undo_stack: list[Transaction]
│       └── marks: dict[str, (int, int)]
│
└── Workspace
    └── TabPage (0..n, one active)
        ├── root: SplitNode        binary split tree
        └── active_leaf: WindowLeaf
            └── Window
                ├── document: Buffer   (reference, not owned)
                ├── cursor: Cursor     (line, col, virtual_col)
                ├── scroll: (int, int) (first visible line, col)
                └── options: dict      (window-local option overrides)
```

Key invariant: **a `Buffer` has no cursor**. Cursors live in `Window`.
Multiple windows can reference the same buffer (`:split` without a filename).

---

## Plugin System

### Loading order
1. `peovim/plugins/` built-in plugins exist as modules but are **not** auto-registered; they require explicit `plugins.load(...)` calls.
2. The user config dir is resolved via `platformdirs.user_config_dir("peovim")` — `~/.config/peovim/` on Linux/Mac, `%APPDATA%\peovim\` on Windows. `init.py` in that directory is executed.
3. `init.py` calls `plugins.load(...)` to activate plugins.
4. Each plugin module must expose `setup(api: EditorAPI) -> None`

### Plugin API surface
```python
# Plugins receive an EditorAPI instance in setup()

editor.buffer           # BufferAPI — current buffer
editor.window           # WindowAPI — current window
editor.mode             # current Mode enum
editor.open_file(path)
editor.split('h'|'v')
editor.close_window()

buffer.path             # str | None
buffer.dirty            # bool
buffer.get_text() -> str
buffer.get_line(n) -> str
buffer.line_count() -> int
buffer.insert(line, col, text)
buffer.delete(line, col, length)
buffer.on('change'|'save'|'load', handler)

window.line, window.col
window.set_cursor(line, col)
window.get_word() -> str
window.add_decoration(decoration: Decoration)
window.clear_decorations(source: str)

keymap.nmap(keys, action)    # action is str (keys) or Callable
keymap.imap(keys, action)
keymap.vmap(keys, action)
keymap.omap(keys, action)    # operator-pending

commands.register(name, handler)  # handler(args: str, range: Range | None)

events.on(event, handler)
# Events: mode_change, buffer_enter, buffer_leave, buffer_change,
#         cursor_move, save_pre, save_post, insert_enter, insert_leave,
#         win_enter, win_leave, tab_enter, tab_leave, vim_leave

ui.show_message(text, level='info'|'warn'|'error')
ui.show_float(id, lines, anchor, max_width, max_height, border=True)
ui.hide_float(id)
ui.show_picker(items, on_select, prompt='')
ui.create_panel(side='left'|'right'|'bottom', width_or_height=30) -> PanelAPI
```

See `notes/api.md` for the full public API surface.

### Plugin example
```python
# ~/.config/peovim/plugins/word_count.py
def setup(api):
    def update_count():
        wc = len(api.buffer.get_text().split())
        api.ui.set_statusline_extra(f'{wc}w')

    api.events.on('buffer_change', update_count)
    api.events.on('buffer_enter', update_count)
```

---

## Syntax Engine

`SyntaxEngine` (`peovim/syntax/engine.py`) manages background parsing via a
`ThreadPoolExecutor`. The main thread submits a `BufferSnapshot`; when the parse
completes, the `on_done` callback is called on the main thread via
`call_soon_threadsafe()`.

```python
class SyntaxEngine:
    def submit(
        self,
        snapshot: BufferSnapshot,
        buffer_id: int,
        on_done: Callable[[int, list[HighlightSpan]], None],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None: ...
```

Stale results are discarded: if a newer version was submitted while the parse
was running, the callback is suppressed.

```
BufferSnapshot submitted
        │
        ▼  (ThreadPoolExecutor worker)
tree-sitter incremental parse + highlight query
        │
        ▼  (call_soon_threadsafe → main thread)
on_done(buffer_id, list[HighlightSpan])
        │
        ▼
EventLoop stores spans; next render frame applies them
        │
        ▼
render_window() receives highlight_spans
        │
        ▼
scope → Style  (via active Theme's scope→color mapping)
        │
        ▼
render_window() applies styles cell-by-cell
```

Highlight query files use `.scm` format (compatible with nvim-treesitter queries).
Built-in grammars: Python, JS/TS, Lua, C/C++, Rust, Go, JSON, YAML, TOML, Markdown, Bash, Verilog, XDC (XDC uses the Bash grammar with a custom query).

---

## LSP Client

```
Editor (asyncio event loop)
    │
    └── LspManager
            ├── filetype → server config registry
            └── LspClient (one per running server)
                    ├── asyncio.subprocess (stdin/stdout JSON-RPC)
                    ├── pending: dict[int, asyncio.Future]
                    ├── _reader coro  — reads stdout, resolves Futures
                    └── _writer coro  — drains outgoing queue

LspClient.request('textDocument/hover', params) -> awaitable
LspClient.notify('textDocument/didChange', params) -> None
```

Results are delivered back to the UI via decorations and floats:
- **Diagnostics** → `VirtualText` + `Sign` decorations on affected lines
- **Hover** → `Float` shown at cursor
- **Completions** → `Float` shown below cursor (completion menu)
- **Signature help** → `Float` shown above cursor
- **Go-to-definition** → `editor.open_file()` + cursor move

---

## Cross-Platform Notes

| Concern | Windows | Linux / Mac |
|---|---|---|
| Terminal I/O (default) | `PromptToolkitBackend` → Win32 Console API | `PromptToolkitBackend` → VT100 |
| Terminal I/O (optional) | `CrosstermBackend` → ConPTY / Win32 | `CrosstermBackend` → VT100 + Kitty |
| Clipboard | `clip.exe` (write), `powershell Get-Clipboard` (read) | `xclip` / `xsel` / `pbcopy` |
| Config path | `%APPDATA%\peovim\init.py` | `~/.config/peovim/init.py` |
| Shell (`!`) | `cmd.exe` or `powershell.exe` | `$SHELL` or `/bin/sh` |
| LSP server paths | PATH lookup, `.exe` extension | Normal PATH |
| Line endings | Normalize `\r\n`/`\r` → `\n` internally on load; track save format separately (`ff=unix|dos|mac`) | `\n` |
| File paths | `pathlib.Path` everywhere | Same |
| Mouse (default) | VT200 via `PromptToolkitBackend` | Same |
| Mouse (crossterm) | SGR + Kitty mouse protocols | Same |

`pathlib.Path` is used universally. `os.path` is never used directly.
All shell invocations use `subprocess` with explicit `shell=False` and arg lists.
Platform detection uses `sys.platform` (`'win32'` / `'linux'` / `'darwin'`), never
string hacks. Clipboard integration lives in `peovim/core/registers.py`
(ctypes WinAPI on Windows, `pbcopy`/`pbpaste` on macOS, `xclip` on Linux).
