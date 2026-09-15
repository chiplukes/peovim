"""Buffer-line <-> visual-row conversion for `VirtualLine` decorations.

Virtual lines (`peovim.ui.decorations.VirtualLine`) insert blank rows into a
window's rendering without being real buffer content (used by `compare.py` to
keep the two diff panes visually aligned when one side has more lines than
the other). Any code that reasons about "which screen row will this buffer
line actually render at" — notably scroll-position math — must account for
them: pure buffer-line arithmetic silently drifts by however many virtual
rows fall between the viewport top and the target line, which is what let
`Window.scroll_to_cursor()` compute a scroll_line that left the cursor's real
line rendered off the bottom of the window (visible as the cursor/text going
"off by a row", including gd/goto-definition landing on the wrong line, after
navigating a diff view — `sync_window_render_state` re-runs
`scroll_to_cursor()` every render frame, so this isn't a one-time glitch).

Used by `peovim.ui.window_render_controller.sync_window_render_state`.
"""

from __future__ import annotations


def merge_virtual_line_spans(anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge (after_line, count) pairs sharing an anchor; return sorted by anchor.

    `after_line == -1` anchors before buffer line 0.
    """
    totals: dict[int, int] = {}
    for after_line, count in anchors:
        if count <= 0:
            continue
        totals[after_line] = totals.get(after_line, 0) + count
    return sorted(totals.items())


def buffer_line_to_visual_row(line: int, spans: list[tuple[int, int]]) -> int:
    """Convert a buffer line index to its visual (rendered) row."""
    visual = line
    for after_line, count in spans:
        if after_line < line:
            visual += count
        else:
            break
    return visual


def visual_row_to_buffer_line(visual_row: int, spans: list[tuple[int, int]]) -> int:
    """Convert a visual row to the buffer line that would render there.

    A visual row that falls *inside* a virtual span (no buffer line renders
    there at all) maps to the first real buffer line after that span — not
    the one before it. Scroll math wants "start rendering from here" values;
    snapping forward guarantees the viewport actually advances past a virtual
    span as the target line moves further away, rather than pinning to the
    span's start regardless of how far past it the target is (which either
    strands the scroll position or forces showing the full span even when
    it's wider than the window has room for).
    """
    vis_pos = 0
    buf_pos = 0
    for after_line, count in spans:
        real_count = 0 if after_line < 0 else (after_line - buf_pos + 1)
        if real_count > 0:
            if visual_row < vis_pos + real_count:
                return buf_pos + (visual_row - vis_pos)
            vis_pos += real_count
            buf_pos += real_count
        if visual_row < vis_pos + count:
            return buf_pos  # inside the virtual span — anchor to the line right after it
        vis_pos += count
    return buf_pos + (visual_row - vis_pos)


def scroll_line_for_cursor(
    *,
    cursor_line: int,
    scroll_line: int,
    height: int,
    scrolloff: int,
    spans: list[tuple[int, int]],
    virtual_skip: int = 0,
) -> int:
    """Virtual-line-aware equivalent of `Window.scroll_to_cursor()`'s vertical logic.

    Returns the buffer-line scroll_line that keeps `cursor_line` visible with
    `scrolloff` rows of margin, accounting for `spans` (virtual-line padding).
    With no spans this reduces to the same values scroll_to_cursor() computes.

    `virtual_skip` is `Window.scroll_virtual_skip` — when the viewport
    deliberately starts partway into a virtual span (compare.py's exact
    cross-pane alignment), the true top-of-viewport visual row is `scroll_line`'s
    own visual row + `virtual_skip` (already counts scroll_line's own
    now-skipped content row — see Window.scroll_virtual_skip), not
    `scroll_line`'s own row — using the latter for the stability check would
    treat a legitimately-stable mid-gap position as needing correction on every
    frame. When this function decides the position IS stable, it returns
    `scroll_line` unchanged (not a recomputed value that merely equals it) so
    the caller can tell "still exactly where compare.py put it, virtual_skip is
    still valid" apart from "landed here anyway, virtual_skip must reset to 0".
    """
    if not spans:
        so = max(0, scrolloff)
        if cursor_line - so < scroll_line:
            return max(0, cursor_line - so)
        if cursor_line + so >= scroll_line + height:
            return max(0, cursor_line + so - height + 1)
        return max(0, scroll_line)

    target_visual = buffer_line_to_visual_row(cursor_line, spans)
    current_visual = buffer_line_to_visual_row(scroll_line, spans)
    viewport_top_visual = current_visual + virtual_skip if virtual_skip > 0 else current_visual
    so = max(0, scrolloff)
    if target_visual - so < viewport_top_visual:
        new_visual = target_visual - so
    elif target_visual + so >= viewport_top_visual + height:
        new_visual = target_visual + so - height + 1
    else:
        return scroll_line
    result = visual_row_to_buffer_line(max(0, new_visual), spans)

    # Even snapping forward (see visual_row_to_buffer_line), a span wider than the
    # whole window can still leave cursor_line off-screen. Advance line-by-line
    # (bounded by cursor_line, so this always terminates) until it actually fits.
    while result < cursor_line and target_visual - buffer_line_to_visual_row(result, spans) >= height:
        result += 1
    return result
