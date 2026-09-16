"""Regression coverage: global scrolloff/sidescrolloff now actually apply to
plain (non-diff) window scrolling.

Background: Window.scroll_to_cursor() read scrolloff/sidescrolloff/tabstop
from Window.options — a window-local *override* dict that nothing ever
populates from the global OptionsStore (options.set("scrolloff", N) in
init.py) — so every caller (search jumps, LSP goto, plain j/k navigation via
window_render_controller.py's non-diff branch, ...) silently behaved as if
scrolloff were always 0. Only the diff view's own code path merged global
options correctly. Fixed by adding optional scrolloff/sidescrolloff/tabstop
params to scroll_to_cursor() and effective_scroll_options() to resolve them
from the real OptionsStore at each of the ~15 call sites — see
notes/architecture.md.
"""

from __future__ import annotations

from test_api import _make_api

from peovim.modal.actions import MoveCursor
from peovim.ui.layout import Rect
from peovim.ui.window_render_controller import WindowRenderController


class _HostStub:
    def __init__(self, editor_state):
        self._editor_state = editor_state


def test_plain_window_respects_global_scrolloff_during_navigation():
    api = _make_api("\n".join(f"line{i}" for i in range(200)))
    api.options.set("scrolloff", 8)

    win = api.active_window()
    win._window.height = 20
    win._window.width = 80

    host = _HostStub(api._editor_state)
    rc = WindowRenderController(host)
    rect = Rect(x=0, y=0, width=80, height=20)
    global_opts = api._editor_state.options.global_as_dict()

    def render() -> None:
        rc.sync_window_render_state(win._window, rect, global_opts=global_opts)

    render()
    api._dispatcher.window = win._window

    for target_line in range(1, 199):
        api._dispatcher.dispatch([MoveCursor(target_line, 0)])
        render()
        top_margin = win._window.cursor.line - win._window.scroll_line
        bottom_margin = win._window.scroll_line + win._window.height - 1 - win._window.cursor.line
        # The cursor must never be closer than `scrolloff` rows to either edge,
        # except where the document itself doesn't have enough lines on that
        # side (start/end of file) to maintain the full margin.
        if win._window.scroll_line > 0:
            assert top_margin >= 8, f"line {target_line}: top margin {top_margin} < scrolloff"
        max_scroll = max(0, 200 - win._window.height)
        if win._window.scroll_line < max_scroll:
            assert bottom_margin >= 8, f"line {target_line}: bottom margin {bottom_margin} < scrolloff"


def test_plain_window_with_no_scrolloff_set_is_unaffected():
    """scrolloff=0 (or unset) must behave exactly as before this fix — cursor
    can reach the literal edge of the window."""
    api = _make_api("\n".join(f"line{i}" for i in range(200)))

    win = api.active_window()
    win._window.height = 20
    win._window.width = 80

    host = _HostStub(api._editor_state)
    rc = WindowRenderController(host)
    rect = Rect(x=0, y=0, width=80, height=20)
    global_opts = api._editor_state.options.global_as_dict()

    def render() -> None:
        rc.sync_window_render_state(win._window, rect, global_opts=global_opts)

    render()
    api._dispatcher.window = win._window

    api._dispatcher.dispatch([MoveCursor(19, 0)])
    render()
    # With no scrolloff, cursor can sit exactly on the last visible row.
    assert win._window.cursor.line - win._window.scroll_line == 19
    assert win._window.scroll_line == 0
