"""Window render-job helpers extracted from `EventLoop`."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from peovim.syntax.engine import HighlightSpan
from peovim.ui.cell_grid import CellGrid
from peovim.ui.layout import Rect
from peovim.ui.render_jobs import (
    RenderExecutionPolicy,
    WindowRenderJob,
    create_window_render_job,
    merge_window_render_results,
    render_execution_policy_from_values,
    render_window_job,
    resolve_render_strategy,
)

if TYPE_CHECKING:
    from peovim.core.snapshot import WindowSnapshot
    from peovim.ui.event_loop import EventLoop


class WindowRenderController:  # cm:9d6c3f
    """Owns render-job collection and assembly for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host
        self._window_grids: WeakKeyDictionary = WeakKeyDictionary()

    def render_window_content(self, grid: CellGrid, tab: object, layout: dict, theme: object) -> None:
        host = self._host
        loop = self.get_syntax_callback_loop()
        policy = self.resolve_render_execution_policy()
        if resolve_render_strategy(allow_parallel=True, policy=policy) == "sequential":
            global_opts = self._global_options_snapshot()
            for leaf, rect in layout.items():
                job = self.build_window_render_job(tab, leaf, rect, theme, loop, global_opts=global_opts)
                cached_grid = self._window_grids.get(leaf.window)
                result = render_window_job(job, grid=cached_grid)
                self._window_grids[leaf.window] = result.grid
                grid.blit(result.grid, dest_x=result.rect.x, dest_y=result.rect.y)
            return
        jobs = self.collect_window_render_jobs(tab, layout, theme, loop)
        merge_window_render_results(
            grid,
            host._render_executor.render_jobs(
                jobs,
                allow_parallel=True,
                policy=policy,
            ),
        )

    def resolve_render_execution_policy(self) -> RenderExecutionPolicy | None:
        host = self._host
        if host._editor_state is None:
            return None
        return render_execution_policy_from_values(
            host._editor_state.options.get("parallelrender"),
            host._editor_state.options.get("parallelrenderworkers"),
        )

    def collect_window_render_jobs(
        self,
        tab: object,
        layout: dict,
        theme: object,
        loop: asyncio.AbstractEventLoop | None,
    ) -> list[WindowRenderJob]:
        global_opts = self._global_options_snapshot()
        jobs: list[WindowRenderJob] = []
        for leaf, rect in layout.items():
            jobs.append(self.build_window_render_job(tab, leaf, rect, theme, loop, global_opts=global_opts))
        return jobs

    def build_window_render_job(
        self,
        tab: object,
        leaf: object,
        rect: Rect,
        theme: object,
        loop: asyncio.AbstractEventLoop | None,
        *,
        global_opts: dict | None = None,
    ) -> WindowRenderJob:
        host = self._host
        self.sync_window_render_state(leaf.window, rect, global_opts=global_opts)
        snapshot = self.snapshot_window_for_render(leaf.window, global_opts=global_opts)
        is_active = leaf.window is tab.active_window
        if is_active and host._should_use_terminal_cursor(snapshot.options):
            snapshot = replace(snapshot, options={**snapshot.options, "_paint_cursor": False})
        self.submit_window_syntax(snapshot, leaf.window, loop)
        highlight_spans = self.resolve_window_highlight_spans(
            leaf.window.document,
            snapshot.scroll_line,
            snapshot.scroll_line + max(0, rect.height - 1),
        )
        decorations = self.build_window_render_decorations(leaf.window, snapshot, is_active)
        return create_window_render_job(
            snapshot,
            rect,
            is_active=is_active,
            decorations=decorations,
            highlight_spans=highlight_spans,
            theme=theme,
            sign_registry=host._editor_state.sign_registry if host._editor_state else None,
        )

    def sync_window_render_state(self, window: object, rect: Rect, *, global_opts: dict | None = None) -> None:
        window.width = rect.width
        window.height = rect.height
        max_scroll = max(0, window.document.line_count() - rect.height)
        window.scroll_line = max(0, min(window.scroll_line, max_scroll))
        if getattr(window, "follow_cursor", True):
            window.scroll_to_cursor(text_width=self._text_width_for_window(window, rect.width, global_opts=global_opts))

    def snapshot_window_for_render(self, window: object, *, global_opts: dict | None = None) -> WindowSnapshot:
        return window.snapshot(global_options=global_opts)

    def _global_options_snapshot(self) -> dict | None:
        host = self._host
        return host._editor_state.options.global_as_dict() if host._editor_state is not None else None

    def _text_width_for_window(self, window: object, rect_width: int, *, global_opts: dict | None = None) -> int:
        """Return the visible text columns for a window (rect_width minus gutter)."""
        if global_opts is None:
            host = self._host
            global_opts = host._editor_state.options.global_as_dict() if host._editor_state else {}
        opts = {**global_opts, **getattr(window, "options", {})}

        line_count = window.document.line_count()
        gutter_w = 0
        if opts.get("number") or opts.get("relativenumber"):
            gutter_w += max(len(str(line_count)), 3) + 1  # digits + separator
        signcolumn = opts.get("signcolumn", "auto")
        if signcolumn == "yes":
            gutter_w += 2
        elif signcolumn == "auto":
            # Conservatively assume signs may be present; avoids cursor hiding
            # behind the sign column. Worst case: scrolls 2 cols earlier than needed.
            gutter_w += 2

        return max(1, rect_width - gutter_w)

    def submit_window_syntax(
        self,
        snapshot: WindowSnapshot,
        window: object,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        host = self._host
        buf_id = id(window.document)
        buf_version = snapshot.buffer_snapshot.version
        if host._syntax_submitted.get(buf_id) == buf_version:
            return
        host._syntax_submitted[buf_id] = buf_version
        host._syntax_engine.submit(
            snapshot.buffer_snapshot,
            buf_id,
            host._on_syntax_done,
            loop,
        )

    def resolve_window_highlight_spans(
        self,
        document: object,
        visible_start: int,
        visible_end: int,
    ) -> tuple[HighlightSpan, ...]:
        host = self._host
        cached = host._syntax_cache.get(id(document))
        if cached is None or visible_end < visible_start:
            return ()

        if len(cached) == 2:  # compatibility with older test fixtures/cache state
            _version, spans = cached  # type: ignore[misc]
            return tuple(spans)

        _version, _spans, spans_by_line = cached
        visible_spans: list[HighlightSpan] = []
        seen: set[HighlightSpan] = set()
        for line in range(visible_start, visible_end + 1):
            for span in spans_by_line.get(line, ()):
                if span not in seen:
                    seen.add(span)
                    visible_spans.append(span)
        return tuple(visible_spans)

    def build_window_render_decorations(
        self,
        window: object,
        snapshot: WindowSnapshot,
        is_active: bool,
    ) -> tuple[object, ...]:
        decorations = list(self._build_search_decorations(snapshot))
        decorations.extend(self.get_window_extra_decorations(window))
        if is_active:
            decorations.extend(self._build_visual_selection(snapshot))
        return tuple(decorations)

    def _build_visual_selection(self, snapshot: WindowSnapshot) -> list:
        """Build HighlightRegion decorations for the current visual selection."""
        from peovim.modal.engine import Mode
        from peovim.ui.decorations import HighlightRegion, Style

        host = self._host
        mode = host._engine.mode
        if mode not in (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK):
            return []

        style = Style(fg=None, bg=(70, 100, 170))
        return [
            HighlightRegion(start_line, start_col, end_line, end_col, style)
            for start_line, start_col, end_line, end_col in host._engine.visual_selection_regions(
                cursor=(snapshot.cursor_line, snapshot.cursor_col)
            )
        ]

    def _build_search_decorations(self, snapshot: WindowSnapshot) -> list:
        """Build decorations for visible search matches or substitute preview."""
        from peovim.core.search import compile_pattern, search_all_in_line
        from peovim.ui.decorations import HighlightRegion, Style
        from peovim.ui.window_renderer import _extract_visible_lines

        host = self._host
        visible_start = snapshot.scroll_line
        visible_end = visible_start + max(0, snapshot.height - 1)

        def _visible_lines() -> list[str]:
            lines_by_number = _extract_visible_lines(snapshot, visible_start, visible_end)
            return [lines_by_number.get(line_no, "") for line_no in range(visible_start, visible_end + 1)]

        # --- Substitute preview (:s/pat/rep) while cmdline is active ---
        if host._cmdline.active and host._cmdline.prompt == ":":
            from peovim.ui.event_loop import _build_sub_preview_decorations, _parse_sub_preview

            sub = _parse_sub_preview(host._cmdline.text)
            if sub is not None:
                pat_str, replacement, _flags, all_lines, visual_range = sub
                if pat_str:
                    try:
                        compiled = compile_pattern(pat_str)
                    except Exception:
                        compiled = None
                    if compiled:
                        line_range: tuple[int, int] | None = None
                        if visual_range:
                            last_sel = getattr(host._engine, "_last_visual_selection", None)
                            if last_sel is not None:
                                _, anchor, cursor_pos = last_sel
                                line_range = (
                                    min(anchor[0], cursor_pos[0]),
                                    max(anchor[0], cursor_pos[0]),
                                )
                        return _build_sub_preview_decorations(
                            _visible_lines(),
                            snapshot.scroll_line,
                            compiled,
                            replacement,
                            cursor_line=snapshot.cursor_line,
                            all_lines=all_lines,
                            line_range=line_range,
                        )
            return []

        # --- Incsearch: live highlight while /pat or ?pat cmdline is open ---
        compiled = None
        if host._cmdline.active and host._cmdline.prompt in ("/", "?") and host._cmdline.text:
            import contextlib

            with contextlib.suppress(Exception):
                compiled = compile_pattern(host._cmdline.text)
        elif (
            host._editor_state is not None
            and host._editor_state.search.hlsearch_active
            and host._editor_state.search.compiled is not None
        ):
            compiled = host._editor_state.search.compiled

        # --- Confirm-substitute: highlight pending matches + current match specially ---
        if host._editor_state is not None and host._editor_state.confirm_sub is not None:
            cs = host._editor_state.confirm_sub
            pending_style = Style(fg=(0, 0, 0), bg=(255, 200, 0))  # yellow — pending matches
            current_style = Style(fg=(255, 255, 255), bg=(200, 80, 0))  # orange — current match
            conf_decs: list = []
            for idx in range(cs.current_idx, len(cs.matches)):
                m_line, m_start, m_end, _ = cs.matches[idx]
                if visible_start <= m_line <= visible_end:
                    style = current_style if idx == cs.current_idx else pending_style
                    conf_decs.append(HighlightRegion(m_line, m_start, m_line, m_end, style))
            return conf_decs

        if compiled is None:
            return []

        search_style = Style(fg=(0, 0, 0), bg=(255, 200, 0))
        decorations: list = []
        for i, line_text in enumerate(_visible_lines()):
            doc_line = snapshot.scroll_line + i
            for col_start, col_end in search_all_in_line(line_text, compiled):
                decorations.append(HighlightRegion(doc_line, col_start, doc_line, col_end, search_style))
        return decorations

    def get_window_extra_decorations(self, window: object) -> list[object]:
        host = self._host
        if host._editor_state is None:
            return []
        decorations = list(host._editor_state.decorations.get_for_buffer(id(window.document)))
        decorations.extend(host._editor_state.decorations.get_for_buffer(id(window)))
        return decorations

    @staticmethod
    def get_syntax_callback_loop() -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None
