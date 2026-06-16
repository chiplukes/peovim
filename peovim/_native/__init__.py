"""
peovim._native — optional compiled extension package.

HAS_NATIVE is True when the Cython (or Rust) extension was successfully
compiled and imported. All accelerated modules fall back to pure Python when
this is False, so the editor always runs without a compiler.
"""

from __future__ import annotations

try:
    import importlib

    importlib.import_module("peovim._native.cell_grid")
    HAS_NATIVE = True
except ImportError:
    HAS_NATIVE = False

__all__ = ["HAS_NATIVE"]
