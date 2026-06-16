# Python File Overview

Authoritative file listing for the `peovim` package. Consult before adding new files
to avoid duplicating functionality. One-line description of each module's purpose.

---

## `peovim/` — top-level package

| File | Purpose |
|---|---|
| `__init__.py` | Package marker |
| `main.py` | Entry point: `uv run peovim [file]`; creates all objects, runs user config, wires events, starts editor |
| `api.py` | **Public plugin API** — the only file plugins import; re-exports all namespaces |

## `peovim/git/` — core git wrapper

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports Tier 8 git wrapper types |
| `presentation.py` | Shared git presentation helpers: status colors and marker derivation for explorer/panel surfaces |
| `repository.py` | `GitRepository` — typed repo state (`GitRepoState`, `GitBranchInfo`, `GitStatusEntry`, `GitRemote`, `GitLogEntry`), porcelain parsing, and branch/sync/log subprocess helpers |

---

## `peovim/core/` — pure data layer (zero UI imports)

All modules here are headlessly testable. Nothing in `core/` imports from `ui/`.

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `buffer.py` | `PieceTable` — text storage (bytes); insert/delete/query |
| `document.py` | `Document` — wraps PieceTable + path + encoding + CRLF + undo + version; emits `buffer_changed` |
| `cursor.py` | `Cursor` — (line, col) + virtual column for `j`/`k` across short lines |
| `window.py` | `Window` — viewport: cursor + scroll_offset + Document ref + local options |
| `workspace.py` | `Workspace` — split tree (HSplitNode/VSplitNode/WindowLeaf) + TabPage management |
| `registers.py` | `RegisterStore` — named (`a-z`), numbered (`0-9`), special (`"`, `*`, `+`, `_`, `.`, `:`, `%`, `/`) |
| `marks.py` | `MarkStore` — buffer-local (`a-z`), global (`A-Z`), special (`` ` ``, `.`, `[`, `]`, `<`, `>`) |
| `history.py` | `UndoStack` — `Edit` record groups; undo/redo; compound edit context manager |
| `snapshot.py` | `BufferSnapshot`, `WindowSnapshot` — frozen dataclasses passed to background threads |
| `filetype.py` | `detect_filetype(path)` — filetype string from extension / shebang line |
| `style.py` | `Color = tuple[int,int,int] | None` type alias + `Style` dataclass |
| `shada.py` | Persistent state (global marks, registers, command history, jump list); read/write via `platformdirs` |
| `diffing.py` | `parse_hunks(diff_text)` — unified diff parser for gutter sign placement; shared by gitsigns and svnsigns |
| `persistence.py` | Shared atomic file write helpers used by document/session/store saves |
| `persistence_policy.py` | Shared inventory of persistence surfaces and multi-instance policy classifications |
| `options.py` | `OptionsStore` — global/window/buffer option scopes; typed options with validators |
| `jumplist.py` | `JumpList` — `(path, line, col)` entries; `push`/`back`/`forward`; max_depth=100 |
| `search.py` | `compile_pattern`, `search_next`, `search_all_in_line` — regex search helpers |
| `fold.py` | `FoldStore` — manual fold ranges; create/open/close/toggle/delete |
| `event_bus.py` | `EventBus` — `on`/`off`/`once`/`emit`; token-based unsubscribe |
| `decorations_store.py` | `DecorationsStore` — namespace-keyed `(buf_id, ns)` decoration storage |
| `sign_registry.py` | `SignRegistry` — `SignType(char, style)` registry; `register`/`get` |
| `store_api.py` | `PluginStore` — JSON-backed persistent key-value store per plugin (via `platformdirs`) |
| `editor_state.py` | `EditorState` — global singleton: message, search, shada, decorations, sign_registry, options, event_bus, alt_path, alt_cursor |
| `health.py` | `HealthItem` dataclass; `HealthStore` collecting items from registered checkers |
| `health_checks.py` | Built-in health checker: Python env, tree-sitter, optional deps, render runtime, persistence, user config |
| `transaction.py` | `editor.transaction()` context manager — cross-buffer atomic undo |
| `log_manager.py` | `LogManager` singleton — runtime-configurable logging: ring buffer, optional file output, per-module level filters |
| `recovery.py` | `RecoveryStore` — crash-recovery store for unsaved buffer content; per-session lockfiles |
| `text_edits.py` | Position/range transformation helpers — remap cursor/decoration positions across applied text edits |

---

## `peovim/modal/` — modal engine and key handling

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `actions.py` | All `Action` frozen dataclasses (~40 types) + `PluginContext` dataclass |
| `engine.py` | `ModalEngine` — parses `KeyEvent` stream into `Action` list; key trie; mode FSM (normal/insert/visual/operator-pending/replace) |
| `keybindings.py` | `BindingRegistry` — trie of all registered bindings; `noremap` semantics; `<Plug>` mappings; `get_bindings()` query |
| `motions.py` | All motion functions: `h/j/k/l`, `w/b/e`, `0/$`, `gg/G`, `f/F/t/T`, `%`, `{/}`, `(/)`, `[[/]]`, etc. |
| `operators.py` | Operator handlers: `d`, `y`, `c`, `>`, `<`, `=`, `!`, `g~`/`gu`/`gU` |
| `text_objects.py` | Text object functions: `iw/aw`, `i"/a"`, `i(/a(`, `i{/a{`, `ip/ap`, `is/as`, etc. |
| `dispatcher.py` | `ActionDispatcher` — routes `Action` to handlers; owns dot-repeat; emits buffer/cursor events |
| `dispatcher_buffers.py` / `dispatcher_commands.py` / `dispatcher_ex_commands.py` / `dispatcher_folds.py` / `dispatcher_navigation.py` / `dispatcher_plugins.py` / `dispatcher_repeat.py` / `dispatcher_search.py` / `dispatcher_workspace.py` | `ActionDispatcher` helper modules split by domain (buffer mgmt, command actions, ex commands, folds, navigation, plugin actions, repeat, search, workspace/window actions) |
| `dispatcher_text.py` / `dispatcher_clipboard.py` / `dispatcher_modes.py` | `ActionDispatcher` handler modules for text mutations (insert/delete/replace/case/indent/join/increment/filter), yank/paste/block-insert, and mode transitions + cursor-scroll + undo/redo — registered in `_action_handlers` dispatch table |

---

## `peovim/commands/` — ex command system

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `registry.py` | `CommandRegistry` — maps command names to handlers; abbreviation matching |
| `parser.py` | `parse_ex_command()` — parses `[range][cmd][!][args]`; returns `ParsedCommand` |
| `builtin.py` | All standard ex commands: `:w`, `:q`, `:e`, `:b`, `:ls`, `:s`, `:g`, `:split`, `:set`, `:map`, `:normal`, `:noh`, etc. |

---

## `peovim/syntax/` — tree-sitter integration

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `engine.py` | `SyntaxEngine` — incremental tree-sitter parse via `BufferSnapshot`; runs in `ThreadPoolExecutor`; returns `list[HighlightSpan]` |
| `themes.py` | `Theme` — maps highlight group names to `Style` |
| `languages.py` | Language registry: filetype → tree-sitter grammar + highlight query |
| `queries/` | `.scm` highlight query files; one per language |

---

## `peovim/lsp/` — LSP client

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `client.py` | `LspClient` — async subprocess; JSON-RPC reader/writer; request/response matching; `call_soon_threadsafe` for results |
| `protocol.py` | Content-Length framing; `path_to_uri`/`uri_to_path` (Windows-safe) |
| `features.py` | `LspFeatures` — `initialize`, `did_open/change/save/close`, `hover`, `definition`, `completion`, `references`, `rename`, `_on_diagnostics`; posts results to `result_queue` |
| `manager.py` | `LspManager` — server registry; one server per (filetype, root); background asyncio thread; attach/detach/notify_change/notify_save/restart |

---

## `peovim/debug/` — DAP client (stub; not yet wired)

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `client.py` | `DapClient` stub — async subprocess DAP |
| `profile_workloads.py` | Repeatable profiling harness for representative render, syntax, and atomic-persistence workloads; prints ranked timings for item-49 optimization study |
| `protocol.py` | DAP message type stubs |
| `session.py` | `DebugSession` stub — breakpoints, stack frames, variables |

---

## `peovim/config/` — configuration loading

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `loader.py` | `ConfigLoader` — executes `~/.config/peovim/init.py` (platform-adjusted) once with API injected, then applies trusted project-local `.peovim/init.py` |
| `editorconfig.py` | Reads `.editorconfig`; applies options to buffer on open |
| `project.py` | Project root detection; persisted trust decisions for project-local configs |

---

## `peovim/ui/` — terminal UI framework

Nothing outside `ui/` imports from here directly (plugins use `api.ui`).

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `backend.py` | `TerminalBackend` Protocol + `RenderOp` union type + `KeyEvent`/`MouseEvent` types |
| `backend_factory.py` | `create_backend(name)` — selects implementation |
| `event_loop.py` | `EventLoop` — asyncio main loop: input → engine → dispatch → render; owns syntax/render executor lifecycles; delegates invalidation/render-cycle execution to `RenderCycleController`, input-loop/key-routing helpers to `InputController`, frame layout/theme/body composition to `FrameController`, command-line flow to `CommandLineController`, overlay/widget presentation to `OverlayPresentationController`, terminal cursor visibility/state/render ops to `TerminalCursorController`, window render-job assembly to `WindowRenderController`, LSP-oriented UI helpers to `LspUiAdapter`, and runtime/maintenance/warning helpers to `EventLoopRuntimeController` |
| `render_cycle_controller.py` | `RenderCycleController` — invalidation tracking, grid recreation/render flush, syntax callback cache updates, and `mark_dirty()` extracted from `EventLoop` |
| `input_controller.py` | `InputController` — input-loop handling, key echo/logging, normal-key dispatch, active-window sync, and key-prefix events extracted from `EventLoop` |
| `frame_controller.py` | `FrameController` — frame body composition, layout computation, separator drawing, and theme resolution extracted from `EventLoop` |
| `cmdline_controller.py` | `CommandLineController` — command-line key normalization/result handling, immediate cmdline/picker repaint helpers, transient-message clearing, and command completion source wiring extracted from `EventLoop` |
| `cursor_controller.py` | `TerminalCursorController` — terminal cursor option resolution, effective active-window option merging, screen-state calculation, and render-op generation extracted from `EventLoop` |
| `window_render_controller.py` | `WindowRenderController` — render-job collection/build helpers, visible syntax-span filtering, decoration assembly, and render execution policy handling extracted from `EventLoop` |
| `presentation_controller.py` | `OverlayPresentationController` — overlay key routing plus sidebar/tree/float/picker/completion/which-key presentation helpers extracted from `EventLoop` |
| `lsp_ui_adapter.py` | `LspUiAdapter` — hover/location picker/code-action/workspace-edit/rename/completion/signature-help helpers extracted from `EventLoop` |
| `runtime_controller.py` | `EventLoopRuntimeController` — runtime error reporting, LSP queue draining, maintenance ticks, dirty-render flushing, and parallel-render warning helpers extracted from `EventLoop` |
| `layout.py` | `compute_layout(split_tree, rect) -> dict[WindowLeaf, Rect]` — pure function |
| `cell_grid.py` | `CellGrid` — 2D array of `(char, fg, bg, attrs)`; per-cell dirty tracking; `flush() -> list[RenderOp]` |
| `window_renderer.py` | `render_window(snapshot, rect, ...) -> CellGrid` — pure; handles gutter, syntax, HighlightRegion, OverlayChar, VirtualText, VirtualLine, GhostText, Sign, folds, indent guides, colorcolumn |
| `decorations.py` | Decoration types: `HighlightRegion`, `VirtualText`, `VirtualLine`, `Sign`, `InlayHint`, `GhostText`, `OverlayChar`, `CodeLens`, `Conceal` |
| `markdown.py` | `render_markdown(text) -> list[str]` — strips markdown for hover float display |
| `float_manager.py` | `FloatManager` — positioned floats; z-ordering; focused float keyboard routing; `CursorRelative`/`Centered`/`Absolute` anchors |
| `status_bar.py` | Mode indicator, filename, dirty flag, cursor pos, options display |
| `command_line.py` | `:` input and `/`/`?` search with history recall (`↑`/`↓`) |
| `picker.py` | `PickerWidget` — fuzzy filter (rapidfuzz), preview pane, multi-select; intercepts all keys when open |
| `notify.py` | `NotifyManager` — toast notifications; level colours; top-right stacking; auto-expiry |
| `sidebar.py` | `SidebarHost` + `TreeSidebarPanel` — persistent left sidebar container for explorer and similar navigation panels |
| `completion.py` | `CompletionPopup` — cursor-anchored LSP completion widget; Tab/Enter accept, Esc dismiss, C-n/C-p navigate |
| `tree_view.py` | `TreeView` — `ui.open_tree()` implementation; lazy node expansion; `TreeViewHandle` |
| `terminal_buffer.py` | `TerminalBuffer` — embedded terminal emulator pane (pyte); `ui.open_terminal()` implementation |
| `which_key_panel.py` | `WhichKeyPanel` — pending key prefix display |
| `mouse_dispatcher.py` | `MouseDispatcher` — translates `MouseEvent` to click/scroll/drag actions |
| `render_jobs.py` | `WindowRenderJob`/`RenderExecutionPolicy`/`RenderRuntimeDiagnostics`/`RenderJobExecutor` helpers — immutable per-window render inputs/results, policy-aware strategy selection, shared capability diagnostics, and owned worker-pool lifecycle for gated parallel rendering |
| `panel_host.py` | `PanelHost` — shared base class for sidebar and bottom-panel hosts (tabs, focus, key routing, lifecycle hooks) |
| `bottom_panel.py` | `BottomPanelHost` — VS Code-style bottom panel with tab bar; hosts log output and which-key tabs |
| `ghost_text.py` | `GhostTextManager` — inline suggestion state for AI/Copilot completions |
| `scrollbar.py` | Shared scrollbar geometry and styling helpers |
| `text_layout.py` | Shared helpers for terminal display-column math (wide chars, tabs) |
| `perf_sampler.py` | Lightweight ring-buffer frame timing sampler (feeds perf_panel plugin) |
| `alloc_tracer.py` | tracemalloc-based allocation hotspot logger (opt-in diagnostics) |
| `gc_tracer.py` | GC collection diagnostic logger (opt-in diagnostics) |
| `_cell_grid_pure.py` | Pure-Python `CellGrid` implementation (fallback when native extension absent) |
| `_window_renderer_pure.py` | Pure-Python `render_window()` implementation (fallback when native extension absent) |

### `peovim/ui/backends/`

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `prompt_toolkit.py` | `PromptToolkitBackend` — default; wraps pt input/output; true color |
| `headless.py` | `HeadlessBackend` — programmatic key injection; captures `RenderOp` list; used by all tests |
| `crossterm.py` | `CrosstermBackend` — optional adapter over external `ed_crossterm`; translates provider payloads into `TerminalBackend` events/ops and reports Kitty/terminal capabilities |

---

## `peovim/api/` — public plugin API namespaces

`peovim/api.py` (top-level) re-exports these. Plugins import only from `peovim.api`.

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `_metadata.py` | API version metadata (`VERSION`, `VERSION_STR`) |
| `editor.py` | `EditorAPI` — implemented core facade; `active_buffer`, `active_window`, `active_mode`, `buffer_by_id()`, `list_windows()`, `list_tab_windows()`, `window_by_id()`, `activate_window()`, `open_buffer()`, alternate-file/register/window helpers (`alternate_file()`, `open_alternate_buffer()`, `set/get_register()`, `paste_register()`, `split_window()`, `close_window()`, `only_window()`, `equalize_windows()`), `open_cmdline()`, `set_status()`, `record_jump()`, window-overlay helpers, `set_compare_status()`, `recent_files()`, `push_recent_file()`, `defer()`, `find_files()`, `grep()`, `find_root()`, `get_logger()`, `normal()` |
| `buffer_api.py` | `BufferAPI` — content read/write (`get_line/lines/text`, `set_text()`, `insert/delete/replace`, `batch()`), decorations (`add_highlight/sign/virtual_text/set_ghost_text/clear_namespace`) |
| `window_api.py` | `WindowAPI` — cursor, `set_cursor()`, `scroll_to_cursor()`, `set_scroll_line()`, `visible_range()`, `get/set_option()`, stable `win_id` |
| `workspace_api.py` | `WorkspaceAPI` — tab/window queries |
| `keymap_api.py` | `KeymapAPI` — `nmap/imap/vmap`, `define_plug()`, `invoke_plug()`, binding/group queries (`get_bindings()`, `get_group_name()`), `feed_keys()`, `leader()` |
| `commands_api.py` | `CommandsAPI` — `register()`, `unregister()`, `execute()` |
| `events_api.py` | `EventsAPI` — `on`, `off`, `once`, `emit`, `handler_count`; standard event table |
| `modal_api.py` | `ModalAPI` — `mode()`, `visual_anchor()`, `set_mode()`, `set_visual_anchor()`; replaces direct engine access from plugins |
| `lsp_api.py` | `LspAPI` — experimental; `register_server()`, lifecycle/status helpers (`registered_servers()`, `running_servers()`, `current_buffer_status()`, `attach_buffer()`, `notify_buffer_changed()`, `notify_buffer_saved()`, `attach_open_buffers()`), plus `hover()`, `definition()`, `references()`, `references_search()`, `rename()`, `document_symbol_tree()`, `workspace_symbol_search()`, `goto_next_diag()`, `goto_prev_diag()`, `diag_detail()`, `info()`, `restart()` |
| `completion_api.py` | `CompletionAPI` — planned placeholder; completion source registration API not implemented yet |
| `diagnostics_api.py` | `DiagnosticsAPI` — planned placeholder; unified diagnostics API not implemented yet |
| `snippets_api.py` | `SnippetsAPI` — planned placeholder; snippet expand + tabstop navigation API not implemented yet |
| `git_api.py` | `GitAPI` — facade over `peovim.git`; compatibility methods plus typed repo state, branches, remotes, and branch/sync subprocess helpers |
| `debug_api.py` | `DebugAPI` — planned placeholder; DAP adapter registration API not implemented yet |
| `session_api.py` | `SessionAPI` — experimental session save/restore support |
| `store_api.py` | `StoreAPI` — `get_store(name)` → isolated JSON key-value store |
| `ui_api.py` | `UIAPI` — `notify()`, `show_which_key()`, `hide_which_key()`, `open_float()`, `open_picker()`, `open_tree()`, `open_terminal()` |
| `syntax_api.py` | `SyntaxAPI` — planned placeholder; tree-sitter plugin API not implemented yet |
| `options_api.py` | `OptionsAPI` — `get()`, `set()`, `define()` |
| `registers_api.py` | `RegistersAPI` — `get()`, `set()` |
| `repl_api.py` | `ReplAPI` — `send_line/selection/block()` |
| `testing_api.py` | `TestingAPI` — planned placeholder; runner registration API not implemented yet |
| `quickfix_api.py` | `QuickfixAPI` + `LoclistAPI` — planned placeholder; quickfix/location list API not implemented yet |
| `jumplist_api.py` | `JumplistAPI` — planned placeholder; public jumplist API not implemented yet |
| `diff_api.py` | `DiffAPI` — planned plugin-interface placeholder; not a stable core namespace yet |
| `health_api.py` | `HealthAPI` — `register()` health checker, `set_context()` |

---

## `peovim/plugins/` — built-in plugins

All implemented against the public `peovim.api` — no internal imports.

| File | Purpose |
|---|---|
| `__init__.py` | — |
| `manager.py` | `PluginManager` — `load/unload/list/get`; lazy triggers (on_filetype/on_event/on_command) |
| `autopairs.py` | Auto-close `(`, `[`, `{`, `"`, `'`, `` ` `` in insert mode |
| `commentary.py` | `gcc` / `gc{motion}` comments |
| `surround.py` | `ys`, `cs`, `ds` — add/change/delete surrounding pairs |
| `vcssigns.py` | Shared VCS sign helpers: `register_sign_defs`, `update_signs`, `current_hunks`, `next_hunk`, `prev_hunk` — parameterised by `get_hunks_fn`; used by gitsigns and svnsigns |
| `gitsigns.py` | Gutter signs for added/changed/deleted lines plus the minimal git panel shell (`<leader>gs`) with summary, branches, status, remotes, git-driven diff launch from status rows, and a scratch git log browser |
| `formatter.py` | External formatter runner; triggered by `:Format` / `buffer_pre_save` |
| `lsp.py` | LSP auto-setup: server detection (ty/basedpyright/pylsp/rust-analyzer/tsserver), buffer events, `K`/`gd`/`<leader>gr`/`<leader>rn`/`[d`/`]d` keymaps |
| `picker.py` | Fuzzy picker keymaps (`<leader>ff`, `<leader>fb`, etc.) |
| `which_key.py` | Key binding popup for `\?`; now reads bindings/groups through `KeymapAPI` and shows/hides the panel through `UIAPI` |
| `flash.py` | `FlashPlugin` — `s` + 2-char label overlay jump; `OverlayChar` decorations |
| `session.py` | Session save/restore: `:Session`, `:SessionLoad`, `:SessionList` |
| `todo.py` | Highlight TODO/FIXME/HACK/NOTE comments |
| `guess_indent.py` | Detect `tabstop`/`expandtab` from file content on open |
| `editor_utils.py` | `remember(fn)`/`<leader><leader>` repeat, `<C-^>` alt file, `<leader>pr` paste yank, `<leader>lf/lfc/lfr` file path, `<leader>wv/wc/wf/we` window helpers; now routed through public editor/keymap helpers |
| `dashboard.py` | Startup screen: recent files, sessions, keybinding hints |
| `compare.py` | Diff selection state plus side-by-side diff layout, diff block highlights, inline block hints, diff-local navigation, directed block merges, and diff session refresh/stop UX |
| `explorer.py` | File tree panel using `TreeView` |
| `markers.py` | Named bookmark groups with gutter signs and a sidebar viewer |
| `local_history.py` | Save-triggered local file history snapshots with picker/open/restore/prune commands and per-file retention |
| `diagnostics_panel.py` | Persistent diagnostics sidebar backed by `EditorAPI.list_diagnostics()` |
| `outline.py` | Persistent document outline sidebar backed by LSP document symbols |
| `references_panel.py` | Persistent references sidebar backed by LSP references for the symbol under cursor |
| `workspace_symbols.py` | Persistent workspace-symbol sidebar backed by LSP workspace symbol search |
| `repl.py` | REPL integration: send line/selection/block/cell to terminal |
| `tabs_to_spaces.py` | Normalize literal tab characters to spaces on open and before save |
| `editorconfig.py` | Auto-apply `.editorconfig` on `buffer_opened` |
| `align.py` | Visual-mode line alignment on characters or regexes |
| `codemap.py` | Repo-committed navigation waypoints backed by in-code `cm:` anchor comments; see `notes/codemap.md` |
| `copilot.py` | GitHub Copilot inline completions (ghost text) |
| `copilot_client.py` | Copilot language server client (subprocess JSON-RPC) |
| `filehistory.py` | Per-file local history snapshots browser with diff preview |
| `fquick.py` | Fast file navigation on the `f` prefix (repurposes `f{char}` for session-file cycling/pickers) |
| `perf_panel.py` | Live performance meter bottom-panel tab (frame timings from `ui.perf_sampler`) |
| `proposed_review.py` | Review generated edits as current-vs-proposed diff (built on compare plugin internals) |
| `session_additions.py` | Gutter signs marking lines added since the buffer was opened |
| `svnsigns.py` | SVN working-copy gutter signs, side-by-side diff view, and status sidebar (read-only; mirrors gitsigns) |
| `verilog_lsp/` | Verilog LSP integration package: `plugin.py` (entry), `hierarchy_panel.py` (module hierarchy sidebar), `signal_trace.py` (signal connectivity picker) |
| `notifications.py` | Enhanced notification display stub |
| `calltree.py` | Call hierarchy panel stub |
| `python.py` | Python-specific helpers stub |
| `pytest.py` | pytest runner stub |
| `debugpy.py` | debugpy DAP adapter stub |
| `remote.py` | Remote file URI handler stub |
| `diff.py` | Diff mode stub |
| `spell.py` | Spell checking stub |
| `ai.py` | AI completion stub |

---

## `tests/` — test suite (~2200 tests)

All tests use `HeadlessBackend`. No real terminal required. Run
`uv run pytest --tb=no -q` for the live count; the per-file counts below are
approximate and drift over time.

| File | Tests | Purpose |
|---|---|---|
| `test_buffer.py` | 48 | PieceTable: insert, delete, undo, line index |
| `test_document.py` | 32 | Document: load/save, encoding, CRLF, events |
| `test_cursor.py` | 17 | Cursor movement, virtual column, clamp |
| `test_workspace.py` | 17 | Split tree, tab pages |
| `test_registers.py` | 15 | Register read/write, clipboard, special |
| `test_undo.py` | 15 | Undo/redo, compound edits |
| `test_marks.py` | 20 | Mark set/jump, global marks |
| `test_jumplist.py` | 17 | JumpList push/back/forward, cross-file entries |
| `test_modal.py` | 45 | Engine: key parsing, counts, operators, mode FSM |
| `test_motions.py` | 56 | Every motion function with count variants |
| `test_operators.py` | 14 | Operator+motion combos |
| `test_text_objects.py` | 27 | Text objects inner/outer |
| `test_commands.py` | 29 | Ex command parsing and execution |
| `test_layout.py` | 14 | compute_layout() split trees |
| `test_integration.py` | 26 | End-to-end: HeadlessBackend editing sessions |
| `test_options.py` | 46 | OptionsStore scopes, validators |
| `test_api.py` | 48 | Plugin API surface |
| `test_search.py` | 43 | Search helpers, patterns, hlsearch |
| `test_lsp.py` | 54 | LSP client: mock server, features helpers, completion popup |
| `test_shada.py` | ~30 | Shada read/write |
| `test_session.py` | ~20 | Session save/restore |
| `test_mouse.py` | ~15 | Mouse event dispatch |
| `test_picker.py` | 48 | PickerWidget fuzzy filter, multi-select |
| `test_markdown.py` | 14 | render_markdown() |
| `test_plugin_manager_lazy.py` | ~15 | Lazy plugin load triggers |
| `test_terminal_buffer.py` | ~20 | TerminalBuffer pyte emulation |
| `test_tree_view.py` | ~15 | TreeView expand/collapse |
| `test_plugin_explorer.py` | ~10 | Explorer tree rendering |
| `test_plugin_compare.py` | ~10 | Compare selection state, side-by-side layout, diff decorations, navigation, merges, and compare session UX |
| `test_plugin_diagnostics_panel.py` | ~10 | Diagnostics sidebar registration, refresh, selection |

---

## `notes/` — planning and architecture documentation

| File | Purpose |
|---|---|
| `roadmap.md` | Planned and future work |
| `architecture.md` | Deep-dive: TerminalBackend Protocol, RenderOp, concurrency model, layer rules |
| `api.md` | Full public plugin API surface and design principles |
| `developer_guide.md` | Contributor guide: layer model, startup flow, testing/doc expectations |
| `user_guide.md` | Operational usage and workflows |
| `getting_started.md` | Installation, running, user config, key bindings, troubleshooting |
| `keys.md` | **Single source of truth for keybindings** |
| `vim_compatibility.md` | Vim command coverage reference |
| `python_overview.md` | **This file** — authoritative module listing |
| `roadmap.md` | Planned features and open technical debt |
| `/plugins/plugins.md` | Built-in plugin descriptions and usage |
| `/research/plugin_api_survey.md` | Survey of ~120 plugins; 23 capability groups identified |
| `/plugins/codemap.md` | Codemap plugin: anchor format, map file, workflow |
| `logging.md` | Logging system: LogManager, CLI flags, output panel |
| `native_renderer.md` | Optional Cython native renderer build notes |
