"""
Conditional Cython build for peovim._native extensions.

Cython is declared in [build-system] requires so it is always available in the
isolated build environment when uv/pip installs or syncs the package.  If a C
compiler is absent the build completes silently and the editor falls back to
pure Python.

Build the extensions in-place (editable install):
    uv sync                    # auto-builds on first install / reinstall
    # or manually:
    uv run python setup.py build_ext --inplace
"""
from __future__ import annotations

import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext as _build_ext


class OptionalBuildExt(_build_ext):
    """Skip a C extension silently if the compiler is absent or fails."""

    def build_extension(self, ext: Extension) -> None:
        try:
            super().build_extension(ext)
        except Exception as exc:
            print(
                f"\n[peovim] Warning: could not compile {ext.name}: {exc}\n"
                "  Continuing without the native extension — the editor will\n"
                "  still work using the pure-Python fallback.\n",
                flush=True,
            )


def build_extensions() -> list[Extension]:
    try:
        from Cython.Build import cythonize  # type: ignore[import]
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
