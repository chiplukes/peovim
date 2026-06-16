# Native Renderer

## What It Is

The native renderer is an optional Cython acceleration layer for the two hottest parts of the rendering pipeline. Without it the editor runs fine on pure Python; with it the render hot path drops from ~2 ms/frame to well under 1 ms for typical terminal sizes.

Two operations dominate frame cost:

| File | Key operation | Measured cost (pure Python) | Notes |
|------|--------------|----------------------------|-------|
| `peovim/ui/cell_grid.py` | `flush()` diff loop | ~300–800 µs/frame | O(rows × cols) Python tuple comparison |
| `peovim/ui/cell_grid.py` | `blit()` | ~50–150 µs/frame | row-by-row list slice copy (called per window) |
| `peovim/ui/window_renderer.py` | `render_window()` | ~1–15 ms/frame | full character-by-character line loop |

`flush()` is the densest inner loop: for a 200×50 terminal it performs ~10 000 Python tuple equality checks, `list.append` calls, and `str.join` calls per frame. `render_window()` is the most complex function but also the largest absolute cost.

Cython was chosen because it compiles annotated Python-like source directly to C, shares Python memory management (no GIL/ownership complexity), and lets the pure-Python and Cython implementations share the same interface — the dispatch is invisible to callers.

---

## Fallback Architecture

The native extension is **completely optional**. The overall structure:

```
peovim/_native/__init__.py              # sets HAS_NATIVE bool
peovim/_native/cell_grid.pyx           # Cython source for CellGrid
peovim/_native/window_renderer.pyx     # Cython source for render_window
peovim/ui/_cell_grid_pure.py           # pure-Python CellGrid fallback
peovim/ui/_window_renderer_pure.py     # pure-Python render_window fallback
peovim/ui/cell_grid.py                 # public shim: native → pure
peovim/ui/window_renderer.py           # public shim: native → pure (with scrollbar exception)
```

`HAS_NATIVE` is set by attempting to import `peovim._native.cell_grid` only. If that succeeds the flag is `True`; `window_renderer` is imported independently by its own shim and has no effect on the flag.

### `peovim/ui/cell_grid.py` shim

```python
try:
    from peovim._native.cell_grid import CellGrid
except ImportError:
    from peovim.ui._cell_grid_pure import CellGrid
```

### `peovim/ui/window_renderer.py` shim

The window renderer shim has one extra layer: the native `render_window` does not implement the scrollbar feature, so when `snapshot.options["scrollbar"]` is truthy the shim falls back to the pure implementation even when the native extension is loaded:

```python
from peovim.ui._window_renderer_pure import render_window as _pure_render_window

try:
    from peovim._native.window_renderer import render_window as _native_render_window

    def render_window(*args, grid=None, **kwargs):
        snapshot = args[0] if args else kwargs.get("snapshot")
        if getattr(snapshot, "options", {}).get("scrollbar"):
            return _pure_render_window(*args, grid=grid, **kwargs)
        return _native_render_window(*args, **kwargs)

except ImportError:
    render_window = _pure_render_window
```

All other symbols (`_gutter_width`, `CURSOR_ACTIVE`, `_draw_indent_guides`, etc.) are always re-exported from the pure module so other callsites are unaffected by whether the native extension is present.

---

## Build System

Cython is declared in `[build-system] requires` in `pyproject.toml` so it is always available in the isolated build environment. `setup.py` drives the actual compilation. The key design: `OptionalBuildExt` catches compiler errors and prints a warning rather than failing the install, so a missing C compiler is harmless.

```python
# setup.py (actual implementation)
import sys
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext as _build_ext


class OptionalBuildExt(_build_ext):
    """Skip a C extension silently if the compiler is absent or fails."""

    def build_extension(self, ext):
        try:
            super().build_extension(ext)
        except Exception as exc:
            print(
                f"\n[peovim] Warning: could not compile {ext.name}: {exc}\n"
                "  Continuing without the native extension — the editor will\n"
                "  still work using the pure-Python fallback.\n",
                flush=True,
            )


def build_extensions():
    try:
        from Cython.Build import cythonize
    except ImportError:
        return []

    exts = [
        Extension(
            "peovim._native.cell_grid",
            ["peovim/_native/cell_grid.pyx"],
            extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O3"],
        ),
        Extension(
            "peovim._native.window_renderer",
            ["peovim/_native/window_renderer.pyx"],
            extra_compile_args=["/O2"] if sys.platform == "win32" else ["-O3"],
        ),
    ]
    return cythonize(
        exts,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "nonecheck": False,
        },
    )


setup(
    ext_modules=build_extensions(),
    cmdclass={"build_ext": OptionalBuildExt},
)
```

### Build paths

- `uv sync` — auto-builds the extensions on first install when a C compiler is present. Silently skips if no compiler is found.
- `uv run python setup.py build_ext --inplace` — manual rebuild after editing `.pyx` source.
- `uv sync --reinstall-package peovim` — forces a full reinstall through uv.

`uv sync` only compiles when the `.so`/`.pyd` is absent. After editing `.pyx` source, trigger the manual rebuild.

---

## `flush_ansi()` — Native CellGrid Extra Method

The native `CellGrid` exposes an additional method not present in the pure implementation:

```python
def flush_ansi(self, bytearray out) -> None:
    ...
```

`flush_ansi()` writes ANSI escape sequences directly into the provided `bytearray`, clearing it first. It eliminates all Python object allocation in the hot path: no `MoveCursor`/`PutCells` namedtuples, no `list[RenderOp]`, no f-strings, no `str.join`. The escape sequence encoding is done at the C level using a fast integer-to-ASCII routine.

This method is only available when `HAS_NATIVE` is `True`. Code that calls it must guard with `hasattr(grid, "flush_ansi")` or check `HAS_NATIVE` first.

---

## Testing

There are currently no dedicated tests for the native extension. The test suite runs against pure Python only.

Useful future additions:
- Parametrize key `CellGrid` tests to run against both implementations.
- Property tests using `hypothesis` to verify that `flush()` produces identical `RenderOp` sequences from native and pure.
- A no-compiler CI job: verify that `uv sync` (without a compiler) + `pytest` passes 100%.
