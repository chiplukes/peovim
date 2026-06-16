# Logging

## Overview

Logging is off by default. When enabled, the editor uses Python's standard `logging`
module with hierarchical loggers under the `peovim` root. All log calls are no-ops until
logging is enabled — no string formatting cost when disabled.

---

## Log File

Default location: `~/.config/peovim/peovim.log`

Rotation: `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=2)` — never grows unbounded.

---

## Architecture

### `LogManager`

Central controller singleton (`peovim/core/log_manager.py`) that owns:

```
LogManager
  ├── _file_handler: RotatingFileHandler | None
  ├── _memory_handler: _RingHandler  (always present, ring buffer)
  ├── _active_filters: dict[str, int]  # logger_name → level
  └── _log_path: str
```

Public API:

```python
class LogManager:
    def enable(
        self,
        modules: list[str] | None = None,  # None = all ("peovim")
        level: str = "DEBUG",
        log_path: str | None = None,        # None = use DEFAULT_LOG_PATH
        write_file: bool = True,            # False = ring buffer only, no file
    ) -> str: ...  # returns the log file path, or "" if write_file=False

    def disable(self) -> None: ...

    def set_level(self, level: str, module: str = "peovim") -> None: ...

    def get_log_lines(self, last_n: int = 500) -> list[str]: ...

    def clear(self) -> None: ...  # clears the in-memory ring buffer

    @property
    def log_path(self) -> str: ...

    @property
    def is_active(self) -> bool: ...
```

### `_RingHandler`

A custom `logging.Handler` that stores the last N records in a `deque(maxlen=5000)`.
Used by `:LogView`. Always attached; cost is minimal because the `peovim` root logger's
effective level controls whether records reach it.

```python
class _RingHandler(logging.Handler):
    def __init__(self, maxlen: int = 5000) -> None:
        super().__init__()
        self._buf: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        self._buf.append(self.format(record))

    def get_lines(self) -> list[str]:
        return list(self._buf)

    def clear(self) -> None:
        self._buf.clear()
```

### `_ModuleFilter`

Restricts log output to requested module patterns:

```python
class _ModuleFilter(logging.Filter):
    def __init__(self, patterns: list[str]) -> None:
        # patterns: ["peovim.core", "peovim.ui.event_loop"]  (already stripped of .*)
        self._patterns = patterns

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._patterns:
            return True  # all modules
        return any(
            record.name == p or record.name.startswith(p + ".")
            for p in self._patterns
        )
```

Every module uses the standard idiom:

```python
import logging
log = logging.getLogger(__name__)
```

Then logs with `log.debug(...)`, `log.info(...)`, etc.

---

## CLI Flags

```
peovim --log                          # enable all, level=DEBUG, write to default log path
peovim --log-level=info               # ring-buffer only at INFO (no file written without --log)
peovim --log --log-level=info         # file + ring buffer at INFO
peovim --log-modules=peovim.core.*    # comma-separated module patterns
peovim --log-file=/tmp/peovim.log     # custom log file path (implies --log)
```

`--log-level` without `--log` enables in-panel (ring buffer) logging only — no file is written.

Each module listed in `--log-modules` is imported automatically after its log level is set.
This means modules that register side effects at import time (e.g. `gc.callbacks` hooks,
`atexit` handlers) activate without requiring a dedicated CLI flag. See [Diagnostic Modules](#diagnostic-modules).

---

## In-Editor Commands

### `:LogOn [modules=<patterns>] [level=<level>] [file=no]`

Enable logging. Examples:
```
:LogOn
:LogOn level=debug
:LogOn modules=peovim.core.*
:LogOn modules=peovim.ui.event_loop,peovim.modal.dispatcher level=debug
:LogOn modules=peovim.core.*:debug,peovim.ui:info
:LogOn modules=peovim.ui.event_loop file=no   # ring buffer only, skip file write
```

`file=no` (also `file=off`, `file=false`, `file=0`) enables logging to the in-memory ring buffer only without writing to disk.

### `:LogOff`

Disable logging. Detaches handlers, resets all logger levels.

### `:LogLevel <level> [module=<name>]`

Adjust level without restarting logging. Examples:
```
:LogLevel debug
:LogLevel info module=peovim.ui
```

### `:LogView [last=<n>]`

Writes the ring buffer contents to `~/.config/peovim/ed_logview.txt` and opens it in a horizontal split. Default: last 500 lines.

### `:LogClear`

Clears the in-memory ring buffer.

---

## Log Format

```
14:23:01.123  DEBUG    peovim.ui.event_loop       KEY='<Esc>'  cmdline.active=True
```

Format string:
```python
"%(asctime)s.%(msecs)03d  %(levelname)-7s  %(name)-30s  %(message)s"
```

The date format is `%H:%M:%S` (time only, no date). The logger name field is left-aligned in a 30-character column.

---

## Module Reference

What each logger emits and at what level. Use these names with `--log-modules` or `:LogOn modules=`.

### Startup and plugin loading

| Module | Level | What is logged |
|--------|-------|----------------|
| `peovim.plugins` | INFO | Plugin loaded/unloaded; import or setup failures |
| `peovim.plugins` | DEBUG | Deferred plugin registration (on_filetype/on_event/on_command) |
| `peovim.config` | WARNING | Config file read errors, init.py execution errors |

### Core state changes

| Module | Level | What is logged |
|--------|-------|----------------|
| `peovim.core.options` | DEBUG | Every option set — name, value, scope, win_id/buf_id |
| `peovim.core.event_bus` | DEBUG | Every event emission — name, handler count, kwargs (**high-frequency**: `cursor_moved`, `buffer_changed`, `buffer_text_changed` fire on every edit/movement) |

### UI and panels

| Module | Level | What is logged |
|--------|-------|----------------|
| `peovim.ui.panel_host` | DEBUG | Panel show/hide/focus/blur/tab-cycle for both sidebar and bottom panel |
| `peovim.ui.input_controller` | DEBUG | Raw key codes, engine mode, action taken |
| `peovim.ui.render_cycle_controller` | DEBUG | Render invalidation reasons |
| `peovim.ui.cmdline_controller` | DEBUG | Cmdline key handling, result tracking |
| `peovim.ui.gc_tracer` | DEBUG | GC collection diagnostics — generation, object count at start/stop, top types, collected count (diagnostic module; activate via `--log-modules`) |
| `peovim.ui.alloc_tracer` | DEBUG | tracemalloc-based allocation hotspot logger — logs top allocation sites by object count diff (diagnostic module; **WARNING: causes startup lockup**, see below) |

### Commands and LSP

| Module | Level | What is logged |
|--------|-------|----------------|
| `peovim.commands.registry` | DEBUG | Ex command executed (`:w`, `:LogOn`, etc.) and args; unknown commands |
| `peovim.lsp.client` | DEBUG | LSP wire protocol — stderr from server, unhandled requests, I/O errors |
| `peovim.lsp.features` | DEBUG | Per-method LSP call errors (hover, definition, references, etc.) |
| `peovim.lsp.manager` | DEBUG | Server registration, attach decisions, server-not-found warnings |
| `peovim.api.lsp_api` | DEBUG | Rename and jump-location details |

### Plugins

| Module | Level | What is logged |
|--------|-------|----------------|
| `peovim.plugins.formatter` | INFO | Buffer content changed by formatter |
| `peovim.plugins.formatter` | DEBUG | Formatter command invoked, no-change result |
| `peovim.plugins.formatter` | WARNING | Formatter command not found, non-zero exit, timeout |
| `peovim.plugins.gitsigns` | DEBUG | Sign refresh per buffer — file path and hunk count |
| `peovim.plugins.compare` | DEBUG | Diff compare events |
| `peovim.plugins.editorconfig` | DEBUG/WARNING | EditorConfig detection and per-file application |
| `peovim.plugins.lsp` | DEBUG | Server auto-detection, buffer open events |

---

## Diagnostic Modules

Some modules register low-level hooks (GC callbacks, atexit, etc.) at import time.
They are inert when logging is at WARNING (the default) and activate automatically
when imported via `--log-modules`. No dedicated CLI flag or init.py change is needed.

**Convention for writing a diagnostic module:**

1. Register the hook/callback at module level (runs on import).
2. Guard the callback body with `log.isEnabledFor(logging.DEBUG)` as the first check so the overhead when disabled is a single level-check (~10 ns), not string formatting or GC inspection.

```python
import gc, logging
from collections import Counter

log = logging.getLogger(__name__)

def _gc_callback(phase, info):
    if phase != "start" or not log.isEnabledFor(logging.DEBUG):
        return
    # ... expensive inspection only when debug logging is active

gc.callbacks.append(_gc_callback)
```

**Available diagnostic modules:**

| Module | What it does |
|--------|--------------|
| `peovim.ui.gc_tracer` | Logs GC generation, object count at start/stop, top types, and collected count on each cycle |
| `peovim.ui.alloc_tracer` | tracemalloc diff logger — logs top allocation sites by object count; **causes startup lockup**, use only after editor opens |

### Using `peovim.ui.gc_tracer`

Diagnose GC pressure during scrolling or typing:

```
uv run peovim myfile.py --log --log-modules peovim.ui.gc_tracer
```

Then open `:LogView` or tail `~/.config/peovim/peovim.log`. Each collection logs two lines:

```
GC start gen1  count=(712,3,0)  top: list×18  dict×12  tuple×9
GC stop  gen1  count=(12,3,0)   collected=47  uncollectable=0
```

The `count` tuple is `(gen0, gen1, gen2)` from `gc.get_count()`.  The delta between
`stop-count[0]` and the next `start-count[0]` shows how many new GC-tracked objects
were created between collections.

**Python 3.13+ GC behavior**: Python 3.13 redesigned the GC to run incremental
"safe-point" collections at the end of each asyncio event loop tick — the gen0
700-object threshold no longer applies.  At 60fps the baseline is ~60–120 gen1
collections per 120-frame window (~1 per tick), each sweeping ~20 asyncio
`Future`/`Task` reference-cycle objects in <1ms.  This is normal and harmless.
The `gc_per_frame` metric in the perf panel (OK ≤ 2.0/frame, WARN ≤ 5.0/frame)
accounts for this baseline.

Tunable at runtime:

```python
from peovim.ui import gc_tracer
gc_tracer.top_n = 5       # default 10 — how many types to show
gc_tracer.min_count = 10  # default 3  — suppress types with fewer objects
gc_tracer.log_types = False  # skip type analysis for lower overhead
```

### Using `peovim.ui.alloc_tracer`

> **WARNING**: `tracemalloc` intercepts every Python allocation.  Importing this
> module at startup causes the editor to lock up before it opens (hundreds of
> modules being imported × every allocation = massive overhead).
>
> Only use it on a **running** editor via `:LogOn modules=peovim.ui.alloc_tracer`
> after startup, or for short targeted runs where startup time is irrelevant.

Logs top allocation sites by object-count diff every `snapshot_every` GC collections:

```
=== ALLOC DIFF (every 1 gc, top 15) ===
  +342 obj  asyncio/tasks.py:645
  +120 obj  asyncio/futures.py:89
   +60 obj  peovim/ui/perf_sampler.py:26
```

Tunable at runtime:

```python
from peovim.ui import alloc_tracer
alloc_tracer.snapshot_every = 30  # default 1 — gc collections between snapshots
alloc_tracer.top_n = 15           # default 15 — lines per snapshot
alloc_tracer.min_diff = 5         # default 5 — suppress small-count sites
```

### Adding a new diagnostic module

Place the file anywhere under `peovim/` and activate it via `--log-modules <dotted.path>`.
No code changes to `main.py` or `_init_logging` are needed — the auto-import pattern
handles it. Document the new module in the table above.

---

### Suggested filter combinations

```
# See what loads at startup
:LogOn modules=peovim.plugins level=info

# Trace option changes (e.g. filetype, tabstop from editorconfig)
:LogOn modules=peovim.core.options

# Debug panel visibility issues
:LogOn modules=peovim.ui.panel_host

# Debug key handling
:LogOn modules=peovim.ui.input_controller,peovim.commands.registry

# Debug LSP (protocol-level noise)
:LogOn modules=peovim.lsp

# Debug formatter
:LogOn modules=peovim.plugins.formatter

# Everything except the high-frequency event bus
:LogOn modules=peovim.plugins,peovim.core.options,peovim.ui.panel_host,peovim.commands.registry,peovim.lsp,peovim.plugins.formatter

# All events including high-frequency ones (very noisy)
:LogOn modules=peovim.core.event_bus
```
