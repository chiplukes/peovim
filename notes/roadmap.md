# Roadmap

Planned features and open technical debt. Items here are not yet implemented.

---

## Architecture & Reliability

- **In-editor reload UX** — save-time external-change detection exists; add
  in-editor reload prompts when a file changes while the buffer is open
  (reload / force-write / compare escape hatches)

- **Project-local config trust UX** — the startup `input()` prompt exists;
  refine into a richer in-editor flow: show file contents, offer per-session vs.
  permanent trust, store decision in user-level config

- **Reduce private plugin reach-in** — add public API affordances as new built-ins
  need them; burn down reach-in before it accumulates

- **Project-local persistence** — `.peovim/markers.json` and git cache snapshots
  use atomic replace but no lock or merge discipline across simultaneous instances;
  define ownership and merge strategy for files where concurrent mutations are expected

- **Multi-instance coordination** — if project-local state conflicts become real,
  options include named-pipe instance messaging, lock files with TTLs, or a
  server/client model (one primary instance, others as clients)

- **LSP incremental sync** — `notify_change` currently sends full text on every
  edit; implement `TextDocumentSyncKind.Incremental` using the `Edit` records and
  `PieceTable.line_col_of()` for cheap LSP range construction

- **ModalEngine large method split** — `_feed_visual` (~345 lines) and
  `_resolve_multi_key` (~290 lines) are still complex; lower priority than
  correctness work but worth splitting when touching the modal engine

---

## Planned Plugin API Extensions

API surface additions that would unlock the next tier of third-party plugins.
None require architectural changes — all are additions to the existing decoration,
event, and UI systems.

- **`OverlayChar` decoration** — replace a displayed character temporarily with a
  label char; needed for jump-mode plugins (flash.nvim equivalent)

- **Progress notification API** — `ui.show_progress(id, title, message, pct)` /
  `ui.update_progress` / `ui.hide_progress`; LSP `$/progress` messages drive it;
  rendered as a stacked float in the corner (`FloatManager`)

- **Rich picker API** — `ui.open_picker(PickerConfig)` with fuzzy filtering, preview
  pane, multi-select, async item sources, and per-item custom rendering; replaces the
  current `ui.show_picker()` stub; needs a built-in `peovim.plugins.picker` that
  provides `:find`, `:ls` enhanced, and live grep

- **Snippet engine** — parse VSCode snippet format (`${1:placeholder}`, `$0`, choices,
  variables), track tabstop positions, `Tab`/`Shift-Tab` navigation in Insert mode;
  required for full LSP completion support; exposed as `editor.expand_snippet(text)`

- **Lazy plugin loading** — `plugins.load('...', on_filetype=[...])` /
  `on_command=[...]` / `on_event=[...]`; defers import + `setup()` until the trigger
  fires; `on_command` registers a lazy command that loads and re-dispatches on first use

- **Notification system** — `ui.notify(message, level, timeout, title)` returning a
  dismissable id; renders as an auto-expiring float stack in the top-right corner
  (`FloatManager`); the existing `ui.show_message()` continues for cmdline-area echoes

- **`keymap.get_bindings(prefix, mode)`** — returns all registered bindings under a
  prefix as `list[BindingInfo]`; required for a which-key-style hint popup; the
  keybinding registry already stores all the needed data

---

## Native & Performance

- **Free-threaded parallel rendering** — `WindowRenderController` already dispatches
  per-window jobs to `RenderJobExecutor` with `allow_parallel=True`, gated by the
  `parallelrender` option; prerequisites before enabling in production: verify
  `prompt_toolkit` and `tree-sitter` thread-safety under 3.13t, wait for
  free-threaded Python to exit experimental status (expected 3.14+), profile to
  confirm `render_window()` is still the bottleneck

- **Piece-table storage in Rust** — O(log n) today; a Rust piece table with a stable
  Python API would improve large-file performance; measure before porting

- **NotcursesBackend** — wrapping `py_notcurses` would enable Sixel and Kitty
  Graphics Protocol image rendering; `TerminalBackend` already declares
  `supports_sixel()` and `supports_kitty_graphics()` capability flags; lower priority
  than image rendering design work itself

---

## AI Integration

- `AiAPI` + background thread (Claude API)
- Ghost text completions (300ms debounce)
- `:AIFix` / `:AIExplain` commands
- AI chat panel

---

## HDL/FPGA Features

Blocked on an external HDL LSP server existing.

- Plugin entry point + filetype guard
- Port inspector (simplest float)
- Signal navigator (trace/driver/sinks)
- Module hierarchy viewer (split panel)
- Net graph visualization (box-drawing)
