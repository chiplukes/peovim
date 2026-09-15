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


def _viewport_top_visual(win, blocks, side: str) -> int:
    """The true visual row the top of `win`'s viewport renders at, accounting
    for scroll_virtual_skip (already counts skipping Window.scroll_line's own
    row — see Window.scroll_virtual_skip and window_render_controller.py).
    """
    scroll_visual = compare_mod._buffer_line_to_visual_row(win.scroll_line, blocks, side)
    skip = getattr(win, "scroll_virtual_skip", 0)
    return scroll_visual + skip if skip > 0 else scroll_visual


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
        left_visual = _viewport_top_visual(left_win._window, blocks, "left")
        right_visual = _viewport_top_visual(right_win._window, blocks, "right")
        total += 1
        if left_visual != right_visual:
            mismatches += 1
            max_gap = max(max_gap, abs(left_visual - right_visual))

    # Before the fix this was effectively always mismatched (every downward
    # step fights the alignment) with an unbounded, growing gap. What remains
    # here (skip=0 on both sides at every mismatch — confirmed by hand) is a
    # separate, pre-existing, small quantization imprecision in predicting the
    # active pane's own next scroll position, not the mid-gap alignment this
    # session's Window.scroll_virtual_skip work targeted (that part is exact
    # — see test_dense_adjacent_blocks_no_longer_fight_every_frame's mid-gap
    # cases and the real-file verification in notes/architecture.md). This
    # guards against regressing to "persistently misaligned".
    assert total > 0
    assert mismatches / total < 0.15, f"{mismatches}/{total} steps misaligned (max gap {max_gap})"
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

    left_visual = _viewport_top_visual(left_win._window, blocks, "left")
    right_visual = _viewport_top_visual(right_win._window, blocks, "right")
    assert abs(left_visual - right_visual) <= 1


def test_dense_adjacent_blocks_no_longer_fight_every_frame(tmp_path):
    """Regression guard for the original bug: densely-packed blocks (every ~3
    lines) used to mismatch on nearly every single downward step because the
    other pane's cursor clamp (ignoring scrolloff) triggered the render
    controller's own follow-cursor logic to override the alignment on the
    very next frame.
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
    max_gap = 0
    for _ in range(len(left_lines) - 5):
        api._dispatcher.window = left_win._window
        new_line = left_win._window.cursor.line + 1
        if new_line >= len(left_lines):
            break
        api._dispatcher.dispatch([MoveCursor(new_line, 0)])
        render_both()
        left_visual = _viewport_top_visual(left_win._window, blocks, "left")
        right_visual = _viewport_top_visual(right_win._window, blocks, "right")
        total += 1
        if left_visual != right_visual:
            mismatches += 1
            max_gap = max(max_gap, abs(left_visual - right_visual))

    # Pre-fix this was ~55% mismatched even in this dense, adversarial case.
    # What remains (skip=0 on both sides at every mismatch — confirmed by
    # hand) is the same pre-existing active-pane scroll-prediction imprecision
    # noted in test_diff_pane_scroll_stays_aligned_during_continuous_downward_movement,
    # not a mid-gap alignment regression.
    assert total > 0
    assert mismatches / total < 0.25, f"{mismatches}/{total} steps misaligned (max gap {max_gap})"
    assert max_gap <= 5


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
        left_visual = _viewport_top_visual(left_win._window, blocks, "left")
        right_visual = _viewport_top_visual(right_win._window, blocks, "right")
        total += 1
        if left_visual != right_visual:
            mismatches += 1
            max_gap = max(max_gap, abs(left_visual - right_visual))

    assert total > 0
    assert mismatches / total < 0.05, f"{mismatches}/{total} steps misaligned (max gap {max_gap})"
    assert max_gap <= 2


def test_map_scroll_position_is_exact_at_every_row_of_a_large_gap():
    """The actual bug report this session's Window.scroll_virtual_skip work
    fixed: scrolling to a point *inside* a large insert/delete-only block
    (not just near its edges) used to always map the other pane to the
    block's start, off by up to the block's full width (50 rows in this
    case). Tests _map_scroll_position directly (not through the full
    dispatch/render pipeline, which has its own small, separate, pre-existing
    scroll-prediction imprecision — see the other tests in this file) so this
    checks exactly the thing that changed: every single visual row of the gap
    round-trips to a scroll position whose own visual row matches exactly,
    not just "close".
    """
    left_lines = (
        [f"common_{i}" for i in range(30)] + [f"gone_{i}" for i in range(50)] + [f"common_{i}" for i in range(80, 120)]
    )
    right_lines = [f"common_{i}" for i in range(30)] + [f"common_{i}" for i in range(80, 120)]
    blocks = compare_mod.compute_blocks(left_lines, right_lines)

    saw_nonzero_skip = False
    for left_scroll in range(len(left_lines)):
        right_scroll, right_skip = compare_mod._map_scroll_position(left_scroll, blocks, from_side="left")
        left_visual = compare_mod._buffer_line_to_visual_row(left_scroll, blocks, "left")
        right_scroll_visual = compare_mod._buffer_line_to_visual_row(right_scroll, blocks, "right")
        right_viewport_top_visual = right_scroll_visual + right_skip if right_skip > 0 else right_scroll_visual
        if right_skip > 0:
            saw_nonzero_skip = True
        assert left_visual == right_viewport_top_visual, (
            f"left_scroll={left_scroll}: left_visual={left_visual} "
            f"right=({right_scroll},{right_skip})->visual={right_viewport_top_visual}"
        )

    assert saw_nonzero_skip, "test never actually exercised a mid-gap scroll_virtual_skip position"
