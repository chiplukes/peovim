"""TargetChooser — lettered-badge chooser for arbitrary jump targets.

Shows a small badge (A, B, C, ...) on each target that has a screen rect, then
waits for a single keypress: the matching letter (either case) runs that
target's ``activate`` callback; anything else cancels (vim-style). Pushed as a
transient key interceptor on the event loop.

Used by the explorer ("open this file into window A/B/C") and by ``editor_utils``
("<leader>wg" goto window/sidebar/bottom panel).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from peovim.core.style import Style
from peovim.ui.float_manager import Absolute
from peovim.ui.layout import Rect

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BADGE_W = 3
_BADGE_H = 3


@dataclass(frozen=True)
class ChooserTarget:
    """One selectable target in a :class:`TargetChooser`."""

    name: str  # shown in the status hint
    rect: Rect | None  # where to center the badge; None = skip badge, keep label
    activate: Callable[[], None]


class TargetChooser:
    """Show a lettered badge on each target and run the matching target's
    ``activate()`` on a single keypress (either case). Anything else cancels.
    Pushed as a transient key interceptor on the event loop.
    """

    MAX_TARGETS = len(_LABELS)

    def __init__(self, api: EditorAPI, targets: list[ChooserTarget], *, hint_title: str = "Go to") -> None:
        self._api = api
        self._targets = list(targets)
        self._labels: dict[str, ChooserTarget] = {}
        self._float_handles: list[object] = []
        self._active = True

        letter_style = Style(fg=(0, 0, 0), bg=(255, 215, 0), attrs=1)
        last_label = ""
        for i, target in enumerate(self._targets):
            if i >= len(_LABELS):
                break
            letter = _LABELS[i]
            last_label = letter
            self._labels[letter] = target
            if target.rect is None:
                continue
            handle = self._open_badge(api, letter, target.rect, letter_style)
            if handle is not None:
                self._float_handles.append(handle)
        api.push_key_interceptor(self)
        if len(self._labels) > 1:
            hint = f"{hint_title}: press A-{last_label} (any case, Esc cancel)"
        elif self._labels:
            hint = f"{hint_title}: press {last_label} (any case, Esc cancel)"
        else:
            hint = f"{hint_title}: no targets"
        with contextlib.suppress(Exception):
            api.set_status(hint, notify=False)

    @property
    def is_active(self) -> bool:
        return self._active

    def feed_key(self, key: str) -> bool:
        if not self._active:
            return False
        if len(key) == 1 and key.upper() in self._labels:
            self._finish(self._labels[key.upper()])
        else:
            self._finish(None)
        return True

    def _finish(self, target: ChooserTarget | None) -> None:
        self._active = False
        self._cleanup()
        if target is None:
            return
        with contextlib.suppress(Exception):
            target.activate()

    def _cleanup(self) -> None:
        for handle in self._float_handles:
            with contextlib.suppress(Exception):
                handle.close()  # type: ignore[attr-defined]
        self._float_handles.clear()
        with contextlib.suppress(Exception):
            self._api.pop_key_interceptor(self)
        with contextlib.suppress(Exception):
            self._api.set_status("", notify=False)

    @staticmethod
    def _open_badge(api: EditorAPI, letter: str, rect: Rect, style: Style) -> object | None:
        cx = max(0, rect.x + max(0, rect.width) // 2 - _BADGE_W // 2)
        cy = max(0, rect.y + max(0, rect.height) // 2 - _BADGE_H // 2)
        try:
            return api.ui.open_float(
                [[(letter, style)]],
                anchor=Absolute(cx, cy),
                width=_BADGE_W,
                height=_BADGE_H,
                border=True,
                focusable=False,
                z_order=10,
            )
        except Exception:
            return None
