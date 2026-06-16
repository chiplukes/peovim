"""
core.editor_state — Global editor state not owned by any single buffer/window

EditorState holds search state (current pattern, direction, hlsearch flag)
and is shared between the ActionDispatcher (writes) and EventLoop (reads for
rendering decorations).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from peovim.core.decorations_store import DecorationsStore
from peovim.core.event_bus import EventBus
from peovim.core.options import OptionsStore
from peovim.core.shada import ShadaStore
from peovim.core.sign_registry import SignRegistry


class SearchState:
    """Tracks the active search pattern and related flags."""

    def __init__(self) -> None:
        self.pattern: str = ""
        self.direction: Literal["forward", "backward"] = "forward"
        self.hlsearch_active: bool = False
        self._compiled: re.Pattern | None = None

    def set_pattern(
        self,
        pattern: str,
        direction: str,
        ignorecase: bool = False,
        smartcase: bool = False,
    ) -> None:
        """Store pattern and compile it. Invalid regex is silently ignored."""
        from peovim.core.search import compile_pattern

        self.pattern = pattern
        self.direction = direction  # type: ignore[assignment]
        if pattern:
            try:
                self._compiled = compile_pattern(pattern, ignorecase, smartcase)
            except re.error:
                self._compiled = None
        else:
            self._compiled = None
        self.hlsearch_active = bool(pattern) and self._compiled is not None

    @property
    def compiled(self) -> re.Pattern | None:
        return self._compiled


class ConfirmSubState:
    """State for interactive per-match confirm substitution (:s/.../c flag)."""

    def __init__(
        self,
        matches: list[tuple[int, int, int, str]],  # (line, col_start, col_end, replacement)
        replacement: str,
    ) -> None:
        self.matches = matches
        self.replacement = replacement
        self.current_idx: int = 0
        self.applied: int = 0
        self.initialized: bool = False  # set True after first cursor move

    @property
    def done(self) -> bool:
        return self.current_idx >= len(self.matches)

    @property
    def current(self) -> tuple[int, int, int, str] | None:
        if self.done:
            return None
        return self.matches[self.current_idx]


class EditorState:  # cm:7b9e5f
    """Global editor state shared across the UI and dispatcher."""

    def __init__(self) -> None:
        self.search = SearchState()
        self.confirm_sub: ConfirmSubState | None = None
        self._message: str = ""  # displayed in cmdline area when inactive; cleared on next key
        self.message_history: list[str] = []
        self.active_theme: str = "catppuccin"
        self.event_bus: EventBus = EventBus()
        self.decorations: DecorationsStore = DecorationsStore()
        self.sign_registry: SignRegistry = SignRegistry()
        self.options: OptionsStore = OptionsStore(event_bus=self.event_bus)
        self.shada: ShadaStore = ShadaStore()
        self._api: object = None  # set to EditorAPI by EditorAPI.__init__
        self.alt_path: str | None = None  # alternate file (previous buffer path, for :bd)
        self.alt_cursor: tuple[int, int] = (0, 0)  # cursor position saved alongside alt_path
        self.compare_status: dict[str, Any] | None = None
        self.recovery_store: object | None = None  # set to RecoveryStore after startup

    @property
    def message(self) -> str:
        return self._message

    @message.setter
    def message(self, value: str) -> None:
        self._message = value
        if not value:
            return
        if not self.message_history or self.message_history[-1] != value:
            self.message_history.append(value)
            self.message_history[:] = self.message_history[-500:]
