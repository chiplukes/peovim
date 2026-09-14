"""Tests for peovim.core.virtual_lines — buffer-line <-> visual-row conversion."""

from __future__ import annotations

from peovim.core.virtual_lines import (
    buffer_line_to_visual_row,
    merge_virtual_line_spans,
    scroll_line_for_cursor,
    visual_row_to_buffer_line,
)


class TestMergeVirtualLineSpans:
    def test_sorts_by_anchor(self):
        assert merge_virtual_line_spans([(10, 2), (3, 1)]) == [(3, 1), (10, 2)]

    def test_merges_duplicate_anchors(self):
        assert merge_virtual_line_spans([(5, 2), (5, 3)]) == [(5, 5)]

    def test_drops_zero_or_negative_counts(self):
        assert merge_virtual_line_spans([(5, 0), (5, -1), (5, 2)]) == [(5, 2)]

    def test_empty_input(self):
        assert merge_virtual_line_spans([]) == []


class TestBufferLineToVisualRow:
    def test_no_spans_is_identity(self):
        assert buffer_line_to_visual_row(7, []) == 7

    def test_line_before_any_span_is_unaffected(self):
        spans = [(19, 8)]
        assert buffer_line_to_visual_row(10, spans) == 10

    def test_line_after_span_shifts_by_count(self):
        spans = [(19, 8)]
        assert buffer_line_to_visual_row(19, spans) == 19  # anchor line itself: block not yet added
        assert buffer_line_to_visual_row(20, spans) == 28  # 20 real rows (0..19) + 8 virtual

    def test_anchor_before_line_zero(self):
        spans = [(-1, 3)]
        assert buffer_line_to_visual_row(0, spans) == 3

    def test_multiple_spans_accumulate(self):
        spans = [(5, 2), (19, 8)]
        assert buffer_line_to_visual_row(20, spans) == 20 + 2 + 8


class TestVisualRowToBufferLine:
    def test_no_spans_is_identity(self):
        assert visual_row_to_buffer_line(7, []) == 7

    def test_row_before_span_maps_to_same_line(self):
        spans = [(19, 8)]
        assert visual_row_to_buffer_line(10, spans) == 10

    def test_row_inside_span_snaps_forward_to_first_real_line_after_it(self):
        # Unlike snapping to the block's start, forward-snapping guarantees the
        # viewport advances past a virtual span as more of it needs to scroll by —
        # see scroll_line_for_cursor's "span wider than height" test below.
        spans = [(19, 8)]
        for visual_row in range(20, 28):
            assert visual_row_to_buffer_line(visual_row, spans) == 20

    def test_row_after_span_shifts_back_by_count(self):
        spans = [(19, 8)]
        assert visual_row_to_buffer_line(30, spans) == 22

    def test_round_trips_for_real_lines(self):
        spans = [(5, 2), (19, 8)]
        for line in (0, 4, 5, 6, 18, 19, 20, 25, 40):
            visual = buffer_line_to_visual_row(line, spans)
            assert visual_row_to_buffer_line(visual, spans) == line


class TestScrollLineForCursor:
    def test_no_spans_matches_plain_scrolloff_math(self):
        # cursor already has scrolloff margin on both sides: no change needed.
        assert scroll_line_for_cursor(cursor_line=10, scroll_line=2, height=24, scrolloff=8, spans=[]) == 2

    def test_no_spans_scrolls_up_to_restore_margin(self):
        assert scroll_line_for_cursor(cursor_line=10, scroll_line=8, height=24, scrolloff=8, spans=[]) == 2

    def test_no_spans_scrolls_down_to_restore_margin(self):
        result = scroll_line_for_cursor(cursor_line=40, scroll_line=0, height=24, scrolloff=8, spans=[])
        assert result == 40 + 8 - 24 + 1

    def test_keeps_cursor_visible_across_a_virtual_gap(self):
        spans = [(19, 8)]
        result = scroll_line_for_cursor(cursor_line=25, scroll_line=0, height=24, scrolloff=8, spans=spans)
        visual_cursor = buffer_line_to_visual_row(25, spans)
        visual_scroll = buffer_line_to_visual_row(result, spans)
        assert 0 <= visual_cursor - visual_scroll < 24

    def test_span_wider_than_window_still_keeps_cursor_visible(self):
        # A 20-row virtual gap in a 24-row window: showing it from the start would
        # alone nearly fill the viewport. The naive snap-to-anchor result would strand
        # scroll_line at the block's start for a wide range of targets past it (see
        # visual_row_to_buffer_line's forward-snap + the advance-until-visible loop in
        # scroll_line_for_cursor); confirm it doesn't happen for a range of targets.
        spans = [(19, 20)]
        for cursor_line in (20, 25, 30, 35, 40, 50):
            result = scroll_line_for_cursor(cursor_line=cursor_line, scroll_line=0, height=24, scrolloff=8, spans=spans)
            visual_cursor = buffer_line_to_visual_row(cursor_line, spans)
            visual_scroll = buffer_line_to_visual_row(result, spans)
            assert 0 <= visual_cursor - visual_scroll < 24, f"cursor_line={cursor_line}"

    def test_span_before_scroll_and_target_does_not_affect_result(self):
        spans = [(2, 5)]  # entirely above both scroll_line and cursor_line
        with_spans = scroll_line_for_cursor(cursor_line=40, scroll_line=30, height=24, scrolloff=8, spans=spans)
        without_spans = scroll_line_for_cursor(cursor_line=40, scroll_line=30, height=24, scrolloff=8, spans=[])
        assert with_spans == without_spans
