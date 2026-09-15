"""Regression coverage for diff-pane scroll alignment during cursor movement.

Uses the real (non-mocked) Document/Workspace/EditorAPI harness from
test_api.py plus the real WindowRenderController — the mocked harness in
test_plugin_compare.py can't exercise this, since the bug lives in the
interaction between compare.py's on_cursor_moved and the render controller's
per-frame follow-cursor sync, not in compare.py alone.

Background: on_cursor_moved computes the block-aligned scroll position for
the *other* diff pane and clamps its cursor to hold that position stable
through the next render. Before the fix, that clamp ignored scrolloff (so
the render controller's own scrolloff-respecting follow-cursor logic fought
it every frame) and it read the active pane's *previous* frame's scroll_line
(so it always lagged one frame behind during continuous movement). Both
produced persistent, worsening misalignment between the two panes during
plain j/k navigation — see notes/architecture.md's compare.py section.
"""

from __future__ import annotations

import pathlib

from test_api import _make_api

from peovim.modal.actions import MoveCursor, ScrollView
from peovim.plugins import compare as compare_mod
from peovim.ui.layout import Rect
from peovim.ui.window_render_controller import WindowRenderController


class _HostStub:
    def __init__(self, editor_state):
        self._editor_state = editor_state


def _build_diff_session(tmp_path: pathlib.Path, left_lines: list[str], right_lines: list[str], *, height: int = 20):
    api = _make_api()
    compare_mod.setup(api)
    api.options.set("scrolloff", 8)

    left_path = tmp_path / "left.py"
    right_path = tmp_path / "right.py"
    left_path.write_text("\n".join(left_lines) + "\n", encoding="utf-8")
    right_path.write_text("\n".join(right_lines) + "\n", encoding="utf-8")

    api.events.emit("compare_select_slot_path", slot=1, path=str(left_path))
    api.events.emit("compare_select_slot_path", slot=2, path=str(right_path))
    api.keymap.invoke_plug("DiffSelected")

    windows = api.list_windows()
    left_win = next(w for w in windows if w.buffer().path == left_path.resolve())
    right_win = next(w for w in windows if w.buffer().path == right_path.resolve())
    for w in (left_win, right_win):
        w._window.height = height
        w._window.width = 80

    api.activate_window(left_win)
    rc = WindowRenderController(_HostStub(api._editor_state))
    rect = Rect(x=0, y=0, width=80, height=height)
    global_opts = api._editor_state.options.global_as_dict()

    def render_both() -> None:
        rc.sync_window_render_state(left_win._window, rect, global_opts=global_opts)
        rc.sync_window_render_state(right_win._window, rect, global_opts=global_opts)

    render_both()
    blocks = compare_mod.compute_blocks(left_lines, right_lines)
    return api, left_win, right_win, render_both, blocks


def _sparse_diff_lines() -> tuple[list[str], list[str]]:
    """A handful of isolated single-block edits ~20 lines apart — realistic spacing."""
    left_lines: list[str] = []
    right_lines: list[str] = []
    for i in range(40):
        for j in range(20):
            left_lines.append(f"common_{i}_{j}")
            right_lines.append(f"common_{i}_{j}")
        if i % 4 == 0:
            left_lines.append(f"left_only_{i}")
        elif i % 4 == 1:
            right_lines.extend([f"right_only_{i}_x", f"right_only_{i}_y", f"right_only_{i}_z"])
    return left_lines, right_lines


def test_diff_pane_scroll_stays_aligned_during_continuous_downward_movement(tmp_path):
    left_lines, right_lines = _sparse_diff_lines()
    api, left_win, right_win, render_both, blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    mismatches = 0
    total = 0
    max_gap = 0
    for _ in range(len(left_lines) - 5):
        api._dispatcher.window = left_win._window
        new_line = left_win._window.cursor.line + 1
        if new_line >= len(left_lines):
            break
        api._dispatcher.dispatch([MoveCursor(new_line, 0)])
        render_both()
        expected = compare_mod._map_scroll_line(left_win._window.scroll_line, blocks, from_side="left")
        actual = right_win._window.scroll_line
        total += 1
        if actual != expected:
            mismatches += 1
            max_gap = max(max_gap, abs(actual - expected))

    # Before the fix this was effectively always mismatched (every downward
    # step fights the alignment) with an unbounded, growing gap. A handful of
    # single-frame mismatches right at a block transition is a known, bounded
    # edge case — this guards against regressing to "persistently misaligned".
    assert total > 0
    assert mismatches / total < 0.1, f"{mismatches}/{total} steps misaligned (max gap {max_gap})"
    assert max_gap <= 5


def test_diff_pane_scroll_realigns_on_direction_reversal(tmp_path):
    """Moving down then back up should not leave the panes permanently drifted."""
    left_lines, right_lines = _sparse_diff_lines()
    api, left_win, right_win, render_both, blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    api._dispatcher.window = left_win._window
    for _ in range(120):
        new_line = left_win._window.cursor.line + 1
        if new_line >= len(left_lines):
            break
        api._dispatcher.dispatch([MoveCursor(new_line, 0)])
        render_both()

    for _ in range(120):
        new_line = left_win._window.cursor.line - 1
        if new_line < 0:
            break
        api._dispatcher.dispatch([MoveCursor(new_line, 0)])
        render_both()

    expected = compare_mod._map_scroll_line(left_win._window.scroll_line, blocks, from_side="left")
    assert abs(right_win._window.scroll_line - expected) <= 1


def test_dense_adjacent_blocks_no_longer_fight_every_frame(tmp_path):
    """Regression guard for the original bug: densely-packed blocks (every ~3
    lines) used to mismatch on nearly every single downward step because the
    other pane's cursor clamp (ignoring scrolloff) triggered the render
    controller's own follow-cursor logic to override the alignment on the
    very next frame. This doesn't need to be perfect (see the sparse test for
    the tighter bound) — it needs to not be "almost always wrong".
    """
    left_lines: list[str] = []
    right_lines: list[str] = []
    for i in range(30):
        left_lines.append(f"common_{i}_a")
        right_lines.append(f"common_{i}_a")
        left_lines.append(f"common_{i}_b")
        right_lines.append(f"common_{i}_b")
        if i % 3 == 0:
            left_lines.append(f"left_only_{i}")
        elif i % 3 == 1:
            right_lines.extend([f"right_only_{i}_x", f"right_only_{i}_y"])
        left_lines.append(f"common_{i}_c")
        right_lines.append(f"common_{i}_c")

    api, left_win, right_win, render_both, blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    mismatches = 0
    total = 0
    for _ in range(len(left_lines) - 5):
        api._dispatcher.window = left_win._window
        new_line = left_win._window.cursor.line + 1
        if new_line >= len(left_lines):
            break
        api._dispatcher.dispatch([MoveCursor(new_line, 0)])
        render_both()
        expected = compare_mod._map_scroll_line(left_win._window.scroll_line, blocks, from_side="left")
        actual = right_win._window.scroll_line
        total += 1
        if actual != expected:
            mismatches += 1

    # Pre-fix this was ~55% mismatched even in this dense, adversarial case.
    assert total > 0
    assert mismatches / total < 0.2, f"{mismatches}/{total} steps misaligned"


def test_mouse_wheel_scroll_does_not_get_stuck(tmp_path):
    """Regression guard for handle_scroll_view: before the fix, ScrollView
    (mouse wheel / <C-d>/<C-u>) left the cursor right at the viewport edge
    with no scrolloff margin, so the render controller's own scrolloff-
    respecting follow-cursor logic pulled scroll_line straight back to
    satisfy the margin on the very next frame — silently undoing every
    wheel tick and permanently freezing the viewport far short of the end.
    """
    left_lines, right_lines = _sparse_diff_lines()
    api, left_win, right_win, render_both, blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    api._dispatcher.window = left_win._window
    scrolls_seen = []
    for _ in range(150):
        api._dispatcher.dispatch([ScrollView(3)])
        render_both()
        scrolls_seen.append(left_win._window.scroll_line)

    # It must have kept advancing well past where it got stuck pre-fix (line
    # 63-ish in the real-file repro that found this) rather than flatlining
    # early. Check the back half of the run isn't just one repeated value.
    assert len(set(scrolls_seen[-30:])) > 1 or scrolls_seen[-1] >= len(left_lines) - left_win._window.height


def test_mouse_wheel_scroll_up_from_bottom_reaches_top(tmp_path):
    """Regression guard for a second, subtler version of the same bug: an
    *independently computed* inverse clamp (buffer_line_to_visual_row → adjust
    → visual_row_to_buffer_line) can disagree with what scroll_line_for_cursor
    itself decides, because visual_row_to_buffer_line intentionally collapses
    every visual row inside a virtual span to one buffer line — non-invertible
    right at a span. That let scrolling *up* after reaching the bottom get
    trapped a few hundred lines short of the top (found via the real
    exposure_gain_curves_auto.py/_dualgain.py diff, which has a virtual span
    right where this got stuck). The fix uses scroll_line_for_cursor as the
    oracle directly instead of guessing an inverse.
    """
    left_lines, right_lines = _sparse_diff_lines()
    api, left_win, right_win, render_both, _blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    api._dispatcher.window = left_win._window
    for _ in range(len(left_lines)):
        api._dispatcher.dispatch([ScrollView(3)])
        render_both()

    at_bottom = left_win._window.scroll_line
    assert at_bottom > 0

    stuck_streak = 0
    prev = -1
    for _ in range(len(left_lines)):
        api._dispatcher.dispatch([ScrollView(-3)])
        render_both()
        current = left_win._window.scroll_line
        stuck_streak = stuck_streak + 1 if current == prev else 0
        prev = current
        if current == 0:
            break
        # Pre-fix this froze permanently (hundreds of consecutive identical
        # values) well before reaching 0.
        assert stuck_streak < 30, f"scroll stuck at {current} (started from {at_bottom})"

    assert left_win._window.scroll_line == 0


def test_diff_pane_scroll_alignment_survives_mouse_wheel(tmp_path):
    left_lines, right_lines = _sparse_diff_lines()
    api, left_win, right_win, render_both, blocks = _build_diff_session(tmp_path, left_lines, right_lines)

    api._dispatcher.window = left_win._window
    mismatches = 0
    total = 0
    max_gap = 0
    for _ in range(150):
        api._dispatcher.dispatch([ScrollView(3)])
        render_both()
        expected = compare_mod._map_scroll_line(left_win._window.scroll_line, blocks, from_side="left")
        actual = right_win._window.scroll_line
        total += 1
        if actual != expected:
            mismatches += 1
            max_gap = max(max_gap, abs(actual - expected))

    assert total > 0
    assert mismatches / total < 0.1, f"{mismatches}/{total} steps misaligned (max gap {max_gap})"
    assert max_gap <= 5
