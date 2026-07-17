"""
core.registers — Vim register store

Named (a-z, A-Z for append), numbered (0-9), and special registers:
  "  unnamed/default     *  primary clipboard    +  clipboard
  _  black hole          .  last insert text      :  last command
  %  current filename    /  last search pattern   =  expression
Platform-aware clipboard: ctypes WinAPI on Windows, pbcopy/pbpaste on macOS, xclip on Linux.

See notes/architecture.md for the component design overview.
"""

from __future__ import annotations

import subprocess
from typing import Literal

# Register content type
RegKind = Literal["char", "line", "block"]

# Read-only registers (cannot be written directly)
_READ_ONLY = frozenset({".", "%"})

# Special writable registers
_SPECIAL_WRITABLE = frozenset({'"', "/", ":", "*", "+", "=", "#", "-"})


def _is_wsl() -> bool:
    """Return True when running under Windows Subsystem for Linux."""
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _build_linux_read_candidates() -> list[list[str]]:
    """Build an ordered list of candidate clipboard-read commands for Linux."""
    import os

    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-paste", "--no-newline"])
    if os.environ.get("DISPLAY"):
        candidates.extend(
            [
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            ]
        )
    if not candidates:
        candidates = [
            ["wl-paste", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ]
    return candidates


def _build_linux_write_candidates() -> list[list[str]]:
    """Build an ordered list of candidate clipboard-write commands for Linux."""
    import os

    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        candidates.extend(
            [
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ]
        )
    if not candidates:
        candidates = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    return candidates


class RegisterStore:  # cm:f3d2c6
    """
    Stores all Vim registers.

    - Named: a-z; uppercase (A-Z) means append to lowercase
    - Numbered: 0-9; "0" = last yank; "1"-"9" = delete history
    - Special: " (unnamed), * + (clipboard), _ (black hole), . : % / = # -
    """

    def __init__(self) -> None:
        # (text, kind) per register name (lowercase)
        self._store: dict[str, tuple[str, RegKind]] = {}
        # Clipboard access disabled by default; set by environment at startup
        self._clipboard_enabled: bool = False
        # Timestamp (monotonic) of last write to each clipboard register.
        self._clipboard_write_time: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Get / set
    # ------------------------------------------------------------------

    def get(self, name: str) -> tuple[str, RegKind]:
        """Return (text, kind) for register `name`. Empty string if not set."""
        if name == "_":
            return ("", "char")
        if name == "*" or name == "+":
            import time

            clipboard_text = self._read_clipboard()
            cached = self._store.get(name)
            if cached is not None:
                if cached[0] == clipboard_text:
                    return clipboard_text, cached[1]
                # Clipboard changed externally since our last write.
                # If our write was recent (within 2 s), prefer the cached
                # yank over the external change; otherwise trust the fresh
                # clipboard (e.g. user copied from a browser).
                write_time = self._clipboard_write_time.get(name, 0)
                if time.monotonic() - write_time < 2.0:
                    return cached
            return clipboard_text, "char"
        if name in _READ_ONLY and name not in self._store:
            return ("", "char")
        entry = self._store.get(name.lower(), ("", "char"))
        return entry

    def set(self, name: str, text: str, kind: RegKind) -> None:
        """Write `text` into register `name`."""
        if name == "_":
            return  # black hole — discard
        if name in _READ_ONLY:
            return  # silently ignore

        # Uppercase letter = append to named register
        if name.isupper():
            lower = name.lower()
            existing_text, existing_kind = self._store.get(lower, ("", kind))
            self._store[lower] = (existing_text + text, existing_kind)
            return

        # Named or special register
        if name == "*" or name == "+":
            import time

            self._write_clipboard(text)
            self._store[name] = (text, kind)
            self._clipboard_write_time[name] = time.monotonic()
            return

        self._store[name.lower()] = (text, kind)

    def shift_numbered(self, text: str, kind: RegKind = "char") -> None:
        """
        Shift numbered registers: old "1" → "2", "2" → "3", ..., "8" → "9".
        Store new text in "1". Called after a delete operation.
        """
        for i in range(9, 1, -1):
            src = str(i - 1)
            dst = str(i)
            if src in self._store:
                self._store[dst] = self._store[src]
        self._store["1"] = (text, kind)

    def list_registers(self) -> dict[str, tuple[str, RegKind]]:
        """Return all non-empty registers."""
        return {k: v for k, v in self._store.items() if v[0]}

    # ------------------------------------------------------------------
    # Clipboard (platform-aware, best-effort)
    # ------------------------------------------------------------------

    def _read_clipboard(self) -> str:
        """Read from the system clipboard. Returns empty string on failure."""
        try:
            import sys

            if sys.platform == "win32":
                return self._read_clipboard_win32()

            if sys.platform == "darwin":
                result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
                return result.stdout if result.returncode == 0 else ""

            candidates: list[list[str]] = _build_linux_read_candidates()
            candidates.append(["powershell.exe", "-NoLogo", "-Command", "[Console]::Out.Write((Get-Clipboard))"])

            for cmd in candidates:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        return result.stdout
                except FileNotFoundError:
                    continue
            return ""
        except Exception:
            return ""

    def _write_clipboard(self, text: str) -> bool:
        """Write to the system clipboard. Returns True on success."""
        try:
            import sys

            if sys.platform == "win32":
                return self._write_clipboard_win32(text)

            if sys.platform == "darwin":
                result = subprocess.run(["pbcopy"], input=text.encode(), timeout=2, check=False)
                return result.returncode == 0

            candidates: list[list[str]] = _build_linux_write_candidates()
            candidates.append(["clip.exe"])

            for cmd in candidates:
                try:
                    result = subprocess.run(cmd, input=text.encode(), timeout=2, check=False)
                    if result.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
            return False
        except Exception:
            return False

    def _read_clipboard_win32(self) -> str:
        import ctypes
        import ctypes.wintypes

        CF_UNICODETEXT = 13

        u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        u32.OpenClipboard.restype = ctypes.wintypes.BOOL
        u32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
        u32.CloseClipboard.restype = ctypes.wintypes.BOOL
        u32.CloseClipboard.argtypes = []
        u32.GetClipboardData.restype = ctypes.c_void_p
        u32.GetClipboardData.argtypes = [ctypes.wintypes.UINT]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.restype = ctypes.wintypes.BOOL
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

        if not u32.OpenClipboard(None):
            return ""
        try:
            handle = u32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            locked = k32.GlobalLock(handle)
            if not locked:
                return ""
            try:
                return ctypes.wstring_at(locked)
            finally:
                k32.GlobalUnlock(handle)
        except Exception:
            return ""
        finally:
            u32.CloseClipboard()

    def _write_clipboard_win32(self, text: str) -> bool:
        import ctypes
        import ctypes.wintypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        u32 = ctypes.windll.user32  # type: ignore[attr-defined]
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.restype = ctypes.wintypes.BOOL
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k32.GlobalFree.restype = ctypes.c_void_p
        k32.GlobalFree.argtypes = [ctypes.c_void_p]
        u32.OpenClipboard.restype = ctypes.wintypes.BOOL
        u32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
        u32.CloseClipboard.restype = ctypes.wintypes.BOOL
        u32.CloseClipboard.argtypes = []
        u32.EmptyClipboard.restype = ctypes.wintypes.BOOL
        u32.EmptyClipboard.argtypes = []
        u32.SetClipboardData.restype = ctypes.c_void_p
        u32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]

        encoded = text.encode("utf-16-le") + b"\x00\x00"
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            return False
        locked = k32.GlobalLock(handle)
        if not locked:
            k32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, encoded, len(encoded))
        k32.GlobalUnlock(handle)
        # OpenClipboard(None) fails if another process holds the clipboard open.
        # This is the primary cause of the intermittent yw/ye + p paste bug.
        if not u32.OpenClipboard(None):
            k32.GlobalFree(handle)
            return False
        try:
            u32.EmptyClipboard()
            result = u32.SetClipboardData(CF_UNICODETEXT, handle)
            if not result:
                # SetClipboardData failed; we still own the handle
                k32.GlobalFree(handle)
                return False
            return True
        finally:
            u32.CloseClipboard()
