"""
editor_utils — Miscellaneous daily-use keymaps not tied to a specific plugin.

Implements:
  remember(fn)  — Wrap a callback so it can be repeated with <leader><leader>
  <leader><leader> — Repeat last remember()-wrapped command
  <C-^>         — Toggle alternate file (swap to/from previous buffer)
  <leader>pr    — Paste from yank register "0" (unaffected by deletes)
  <leader>lf    — Show file info (path, size, extension)
  <leader>lfc   — Copy full path to clipboard
  <leader>lfr   — Copy relative path to clipboard
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any


def setup(api: Any) -> None:
    # ------------------------------------------------------------------
    # remember(fn) — wrap a callback so <leader><leader> can repeat it
    # ------------------------------------------------------------------
    _last_cmd: list[Callable | str | None] = [None]

    def _call_plug(name: str) -> None:
        if not api.keymap.invoke_plug(name):
            plug_name = name[len("<Plug>") :] if name.startswith("<Plug>") else name
            api.ui.notify(f"Unknown plug mapping: <Plug>{plug_name}", level="warn")

    def remember(fn: Callable | str) -> Callable:
        """Return a wrapper that stores fn as the last command before calling it."""

        def _wrapper() -> None:
            _last_cmd[0] = fn
            if isinstance(fn, str):
                _call_plug(fn)
            else:
                fn()

        return _wrapper

    def _repeat_last() -> None:
        if _last_cmd[0] is not None:
            if isinstance(_last_cmd[0], str):
                _call_plug(_last_cmd[0])
            else:
                _last_cmd[0]()
        else:
            api.set_status("No previous command to repeat", notify=False)

    # Register on the public API so _build_namespace exposes them in init.py.
    api.remember = remember
    api.repeat = _repeat_last

    api.keymap.nmap("<Plug>EditorRepeat", _repeat_last, desc="Editor: repeat last command")
    api.keymap.nmap("<leader><leader>", "<Plug>EditorRepeat", desc="Repeat last command")

    # ------------------------------------------------------------------
    # <C-^> — Alternate file toggle
    # ------------------------------------------------------------------
    def _alt_file() -> None:
        api.open_alternate_buffer()

    api.keymap.nmap("<Plug>EditorAltFile", _alt_file, desc="Editor: alternate file")
    api.keymap.nmap("<C-^>", "<Plug>EditorAltFile", desc="Alternate file")

    # ------------------------------------------------------------------
    # <leader>pr — Paste from yank register "0"
    # ------------------------------------------------------------------
    def _paste_yank() -> None:
        api.paste_register("0", before=False)

    api.keymap.nmap("<Plug>EditorPasteYank", _paste_yank, desc="Editor: paste yank register")
    api.keymap.nmap("<leader>pr", "<Plug>EditorPasteYank", desc="Paste from yank register")

    # ------------------------------------------------------------------
    # <leader>lf / <leader>lfc / <leader>lfr — File path info / copy
    # ------------------------------------------------------------------
    def _active_doc_path() -> pathlib.Path | None:
        return api.active_buffer().path

    def _file_info() -> None:
        p = _active_doc_path()
        if p is None:
            api.ui.notify("No file", level="warn")
            return
        try:
            rel = str(p.relative_to(pathlib.Path.cwd()))
        except ValueError:
            rel = str(p)
        size = p.stat().st_size if p.exists() else 0
        api.ui.notify(f"{p}\n{rel}\n{size} bytes  ext={p.suffix or '(none)'}", level="info")

    def _copy_full_path() -> None:
        p = _active_doc_path()
        if p is None:
            return
        text = str(p)
        api.set_register("+", text, "char")
        api.ui.notify(f"Copied: {text}", level="info")

    def _copy_rel_path() -> None:
        p = _active_doc_path()
        if p is None:
            return
        try:
            text = str(p.relative_to(pathlib.Path.cwd()))
        except ValueError:
            text = str(p)
        api.set_register("+", text, "char")
        api.ui.notify(f"Copied: {text}", level="info")

    api.keymap.nmap("<Plug>EditorFileInfo", _file_info, desc="Editor: file info")
    api.keymap.nmap("<Plug>EditorCopyPath", _copy_full_path, desc="Editor: copy full path")
    api.keymap.nmap("<Plug>EditorCopyRel", _copy_rel_path, desc="Editor: copy relative path")
    api.keymap.nmap("<leader>lf", "<Plug>EditorFileInfo", desc="File info")
    api.keymap.nmap("<leader>lfc", "<Plug>EditorCopyPath", desc="Copy full path")
    api.keymap.nmap("<leader>lfr", "<Plug>EditorCopyRel", desc="Copy relative path")

    # ------------------------------------------------------------------
    # Window management: <leader>wv/wc/wf/we
    # ------------------------------------------------------------------
    def _win_vsplit() -> None:
        api.split_window("v")

    def _win_close() -> None:
        api.close_window()

    def _win_only() -> None:
        api.only_window()

    def _win_equalize() -> None:
        api.equalize_windows()

    api.keymap.nmap("<Plug>WinVSplit", _win_vsplit, desc="Window: vertical split")
    api.keymap.nmap("<Plug>WinClose", _win_close, desc="Window: close")
    api.keymap.nmap("<Plug>WinOnly", _win_only, desc="Window: close others")
    api.keymap.nmap("<Plug>WinEqualize", _win_equalize, desc="Window: equalize")
    api.keymap.nmap("<leader>wv", "<Plug>WinVSplit", desc="Vertical split")
    api.keymap.nmap("<leader>wc", "<Plug>WinClose", desc="Close window")
    api.keymap.nmap("<leader>wf", "<Plug>WinOnly", desc="Focus (close others)")
    api.keymap.nmap("<leader>we", "<Plug>WinEqualize", desc="Equalize windows")
