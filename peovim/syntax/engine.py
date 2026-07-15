"""
syntax.engine — SyntaxEngine: tree-sitter wrapper

Runs incremental parsing in a ThreadPoolExecutor background worker.
Input: BufferSnapshot. Output: list[HighlightSpan] posted to main thread
via call_soon_threadsafe(). Discards results whose snapshot.version is stale.

See notes/architecture.md §Concurrency Model for the threading design.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.core.snapshot import BufferSnapshot


# ---------------------------------------------------------------------------
# Lower = painted first (overridden by higher-priority spans at the same position).
# Matches from more-specific query rules (constant, function.builtin) must
# override generic ones (variable) when they overlap on the same node.
_GROUP_PRIORITY: dict[str, int] = {
    "variable": 10,
    "constructor": 12,
    "function": 10,
    "function.builtin": 15,
    "function.method": 12,
    "property": 12,
    "type": 12,
    "constant": 20,
    "constant.builtin": 15,
    "constant.macro": 20,
}
_DEFAULT_GROUP_PRIORITY = 8


@dataclass(frozen=True)
class HighlightSpan:
    """
    A single syntax highlight region produced by tree-sitter.

    Columns are byte offsets (matching BufferSnapshot coordinates). Spans
    may cross lines (e.g. multi-line strings); the renderer handles this.
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int
    group: str  # capture name without leading '@', e.g. 'keyword', 'string'
    priority: int = 10  # lower than search (100) and user decorations (50+)


# ---------------------------------------------------------------------------
# Module-level caches (shared across all SyntaxEngine instances)
# ---------------------------------------------------------------------------

_language_cache: dict[str, Any] = {}  # module_name → Language object
_query_cache: dict[str, Any] = {}  # module_name → Query object

# Tree cache: buf_id → (version, Tree). Owned by worker threads; guarded by _cache_lock.
_tree_cache: dict[int, tuple[int, Any]] = {}
_cache_lock = threading.Lock()


def _get_language(info) -> Any | None:
    """Return a cached tree-sitter Language for the given LanguageInfo."""
    key = f"{info.module_name}:{info.language_attr}"
    if key not in _language_cache:
        lang = info.get_language()
        if lang is None:
            return None
        _language_cache[key] = lang
    return _language_cache[key]


def _get_query(info, lang: Any) -> Any | None:
    """Return a cached tree-sitter Query for the given LanguageInfo."""
    key = f"{info.module_name}:{info.language_attr}"
    if key not in _query_cache:
        q_str = info.get_highlights_query()
        if not q_str:
            return None
        try:
            from tree_sitter import Query

            _query_cache[key] = Query(lang, q_str)
        except Exception:
            return None
    return _query_cache.get(key)


# ---------------------------------------------------------------------------
# Background parse task (runs in ThreadPoolExecutor)
# ---------------------------------------------------------------------------


def _reconstruct_text(snapshot: BufferSnapshot) -> bytes:
    """Rebuild full UTF-8 bytes from a BufferSnapshot's piece list."""
    result = bytearray()
    for piece in snapshot.pieces:
        buf = snapshot.original if piece.buf == "original" else snapshot.add
        result.extend(buf[piece.start : piece.start + piece.length])
    return bytes(result)


def _parse_task(
    snapshot: BufferSnapshot,
    buffer_id: int = 0,
    version_check: Callable[[], int] | None = None,
    visible_end_line: int | None = None,
) -> list[HighlightSpan]:
    """
    Run in ThreadPoolExecutor. Returns sorted list of HighlightSpan or []
    on any failure (missing grammar, parse error, stale version, etc.).
    """
    # Early stale check — avoids full parse when the snapshot is already superseded.
    if version_check is not None and version_check() != snapshot.version:
        return []

    from peovim.syntax.languages import get_language_info

    info = get_language_info(snapshot.filetype)
    if info is None:
        return []

    try:
        lang = _get_language(info)
        if lang is None:
            return []

        query = _get_query(info, lang)
        if query is None:
            return []

        text_bytes = _reconstruct_text(snapshot)

        from tree_sitter import Parser, QueryCursor

        parser = Parser(lang)

        # --- Incremental parse ---
        # Read cached tree without holding the lock during the (slow) parse.
        with _cache_lock:
            cached = _tree_cache.get(buffer_id)
            cached_version, old_tree = cached if cached else (-1, None)

        expected_old = snapshot.version - len(snapshot.pending_edits)
        if old_tree is not None and snapshot.pending_edits and cached_version == expected_old:
            # Apply all recorded edits to a copy of the old tree, then parse.
            tree_copy = old_tree.copy()
            for edit in snapshot.pending_edits:
                tree_copy.edit(
                    edit.start_byte,
                    edit.old_end_byte,
                    edit.new_end_byte,
                    (edit.start_row, edit.start_col),
                    (edit.old_end_row, edit.old_end_col),
                    (edit.new_end_row, edit.new_end_col),
                )
            tree = parser.parse(text_bytes, tree_copy)
        else:
            tree = parser.parse(text_bytes)

        # Store new tree only if it advances the cache (guards against a slow
        # concurrent task overwriting a newer result).
        with _cache_lock:
            existing = _tree_cache.get(buffer_id)
            if existing is None or existing[0] < snapshot.version:
                _tree_cache[buffer_id] = (snapshot.version, tree)

        # --- Query (with visible-range restriction) ---
        cursor = QueryCursor(query)
        if visible_end_line is not None:
            offsets = snapshot.line_offsets
            end_idx = min(visible_end_line, len(offsets) - 1)
            end_byte = offsets[end_idx + 1] if end_idx + 1 < len(offsets) else len(text_bytes)
            cursor.set_byte_range(0, end_byte)

        captures = cursor.captures(tree.root_node)

        spans: list[HighlightSpan] = []
        for capture_name, nodes in captures.items():
            group = capture_name.lstrip("@")
            priority = _GROUP_PRIORITY.get(group, _DEFAULT_GROUP_PRIORITY)
            for node in nodes:
                sp = node.start_point  # (row, col)
                ep = node.end_point
                spans.append(HighlightSpan(sp[0], sp[1], ep[0], ep[1], group, priority=priority))

        # Sort by position, then by priority (higher = painted later, wins overlap).
        spans.sort(key=lambda s: (s.start_line, s.start_col, s.priority))
        return spans

    except Exception:
        return []


# ---------------------------------------------------------------------------
# SyntaxEngine
# ---------------------------------------------------------------------------


class SyntaxEngine:  # cm:5a3c7e
    """
    Manages background syntax parsing.

    Submit a BufferSnapshot; when the parse completes the on_done callback
    is called on the asyncio main thread via call_soon_threadsafe().
    """

    def __init__(self, executor: ThreadPoolExecutor) -> None:
        self._executor = executor
        # buffer_id → latest submitted version (for stale-result discard)
        self._pending_version: dict[int, int] = {}

    def submit(
        self,
        snapshot: BufferSnapshot,
        buffer_id: int,
        on_done: Callable[[int, list[HighlightSpan]], None],
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        visible_end_line: int | None = None,
    ) -> None:
        """
        Submit a parse task for the given snapshot.

        Args:
            snapshot:         Immutable buffer snapshot to parse.
            buffer_id:        Opaque ID distinguishing buffers (e.g. id(document)).
            on_done:          Called on the main thread with (buffer_id, spans) when
                              the parse completes. NOT called if superseded.
            loop:             asyncio event loop for call_soon_threadsafe. If None,
                              uses asyncio.get_event_loop().
            visible_end_line: Restrict query to lines [0, visible_end_line] so we
                              only produce spans for the visible area. None = full file.
        """
        version = snapshot.version
        self._pending_version[buffer_id] = version

        _loop = loop

        def _version_check() -> int:
            return self._pending_version.get(buffer_id, -1)

        def _on_complete(fut: Future) -> None:
            if fut.cancelled():
                return
            # Discard if a newer version was submitted while we were parsing
            if self._pending_version.get(buffer_id) != version:
                return
            try:
                spans = fut.result()
            except Exception:
                spans = []
            nonlocal _loop
            if _loop is None:
                try:
                    _loop = asyncio.get_event_loop()
                except RuntimeError:
                    return
            _loop.call_soon_threadsafe(on_done, buffer_id, spans)

        fut = self._executor.submit(_parse_task, snapshot, buffer_id, _version_check, visible_end_line)
        fut.add_done_callback(_on_complete)

    def remove_buffer(self, buffer_id: int) -> None:
        """Remove cached parse state for a closed buffer."""
        self._pending_version.pop(buffer_id, None)
        with _cache_lock:
            _tree_cache.pop(buffer_id, None)
