# Developer Guide

This guide is for contributors and plugin authors working on the editor internals. For user-focused workflows, see [user_guide.md](user_guide.md). For the broader architecture map, see [architecture.md](architecture.md). For an interactive in-editor tour of the codebase anchored to specific source locations, see [codemap.md](codemap.md).

## Layer model

At a high level:
- `peovim/core/` — pure editor state and storage
- `peovim/modal/` — key sequence resolution and action generation
- `peovim/commands/` — ex commands and command parsing
- `peovim/ui/` — terminal rendering, event loop, overlays, widgets, backends
- `peovim/api/` — public plugin façade
- `peovim/plugins/` — built-in plugins implemented on top of the API
- `peovim/lsp/`, `peovim/git/`, `peovim/syntax/` — service subsystems

## Startup flow

The main startup path is in [peovim/main.py](../peovim/main.py):
1. initialize logging and process-wide exception hooks
2. create the initial `Document`, `Window`, `Workspace`, and core stores
3. create modal engine, dispatcher, command registry, and editor API
4. create LSP manager, UI widgets, and plugin manager
5. load user config via `ConfigLoader`
6. load persisted state from shada
7. create the backend and event loop
8. run the event loop

## Public API

Plugins should work exclusively through `peovim.api`. When adding new built-in features:
- expand the public API rather than reaching into private fields
- if a plugin needs a private attribute more than once, that is a sign a new API method is warranted

All built-in plugins in `peovim/plugins/` should work exclusively through the public API. If a plugin needs a private attribute more than once, that is a sign a new API method is warranted; open items are tracked in [roadmap.md](roadmap.md).

## Rendering model

The rendering stack:
- `Window.snapshot()` creates immutable data for rendering
- `SyntaxEngine` runs background parsing
- `render_window()` in [peovim/ui/window_renderer.py](../peovim/ui/window_renderer.py) is a mostly pure function
- `CellGrid` owns diffing and render-op generation
- `TerminalBackend` isolates prompt-toolkit or native backends
- `render_jobs.py` provides policy and worker-pool seams for parallel rendering

These seams make future native acceleration realistic. Good candidates: piece-table
storage, syntax span production, cell-grid diff generation, terminal backend I/O,
hot rendering loops.

## Event loop sub-controllers

`EventLoop` delegates to focused sub-controllers. Current decomposition:

| Controller | File | Responsibility |
|---|---|---|
| `InputController` | `ui/input_controller.py` | Input loop, key dispatch, active-window sync |
| `OverlayPresentationController` | `ui/presentation_controller.py` | Overlay key routing, sidebar/float/picker/completion/which-key |
| `CommandLineController` | `ui/cmdline_controller.py` | Command-line key handling, result routing, command-list sourcing |
| `LspUiAdapter` | `ui/lsp_ui_adapter.py` | Hover, location picker, code action, rename, completion, signature-help |
| `EventLoopRuntimeController` | `ui/runtime_controller.py` | Error reporting, maintenance ticks, LSP queue draining, dirty-render flushing |
| `CursorController` | `ui/cursor_controller.py` | Terminal cursor resolution and render-op generation |
| `WindowRenderController` | `ui/window_render_controller.py` | Render-job collection, decoration assembly, render execution policy |
| `FrameController` | `ui/frame_controller.py` | Frame body composition, layout, separator drawing, theme resolution |
| `RenderCycleController` | `ui/render_cycle_controller.py` | Invalidation tracking, grid recreation, syntax callback cache |

`EventLoop` itself handles frame scheduling and top-level orchestration only.

## Persistence model

All persisted stores use atomic replace (temp-file + `os.replace()`).

| Store | Location | Policy |
|---|---|---|
| `shada` | user data dir | atomic replace, last-writer-wins |
| sessions | user data dir | atomic replace, last-writer-wins per session name |
| plugin stores | user data dir | atomic replace, last-writer-wins per store name |
| file saves | on disk | atomic replace + save-time external-change detection |
| `.peovim/markers.json` | project root | atomic replace, single-writer |
| `.peovim/git*/...` snapshots | project root | atomic replace, disposable scratch output |

Multiple concurrent instances are supported for editing. Shared persisted state
is last-writer-wins. Project-local helper data (markers, git snapshots) should be
treated as single-writer. Persistence improvements are tracked in [roadmap.md](roadmap.md).

The current persistence policy is also visible from inside the editor via `:checkhealth`
under the Persistence section.

## Plugin development guidance

### Prefer the public API

Use `peovim.api` namespaces instead of importing internals. If you need a missing
capability, add it to the public API rather than tunneling through the current
implementation.

### Think in editor events

Most built-in features are coordinated through:
- buffer lifecycle events
- editor-ready / shutdown hooks
- command registration
- keymap plug definitions
- decorations
- picker/sidebar/float surfaces

### Keep UI-independent logic out of `ui/`

If a feature can be described as state transformation, search, parsing, diff computation,
or storage policy, keep it outside `ui/`.

## Testing expectations

The project is designed to be testable without a real terminal.

Typical commands:
- `uv run pytest --tb=no -q`
- `uv run ruff check <path>`
- `uv run ruff format <path>`

When changing behavior:
- add focused regression tests
- prefer headless event-loop or renderer tests for UI behavior
- preserve public API compatibility where practical

## Documentation expectations

When changing behavior, keep these docs aligned:
- [architecture.md](architecture.md)
- [getting_started.md](getting_started.md)
- [user_guide.md](user_guide.md)
- [developer_guide.md](developer_guide.md)
- [python_overview.md](python_overview.md)

A useful rule:
- `getting_started.md` = first-run and common usage
- `user_guide.md` = operational usage and workflows
- `developer_guide.md` = extension and maintenance guidance
- `architecture.md` = system structure and long-term direction

**Checklist when adding a new module:**
- Add a one-line entry to `python_overview.md` under the appropriate section.
  `tests/test_docs.py::test_python_overview_covers_all_modules` will fail CI
  until the entry is present.
- If the module provides a plugin: add a section to `plugins.md` and keybindings
  to `keys.md` if it registers any.
