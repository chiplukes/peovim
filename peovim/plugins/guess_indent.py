"""
Detect and apply tabstop/expandtab from file content on open.

Scans the first 100 indented lines, counts tab vs space usage, and votes on
the dominant indent size.

Implemented against the public peovim.api — no internal imports.
See notes/plugins.md for plugin development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_SCAN_LINES = 100
_COMMENT_PREFIXES = ("*", "/*", "*/", "//", "#", "--", ";")


def setup(api: EditorAPI) -> None:
    """Subscribe to buffer_opened to auto-detect indentation."""
    api.events.on("buffer_opened", lambda **kw: _on_buffer_opened(api, **kw))
    for buf in api.list_buffers():
        _apply_guess(api, buf)


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _on_buffer_opened(api: Any, **kwargs: Any) -> None:
    buf_id: int | None = kwargs.get("buf_id")
    if buf_id is None:
        return
    for buf in api.list_buffers():
        if buf.buf_id == buf_id:
            _apply_guess(api, buf)
            return


def _apply_guess(api: Any, buf: Any) -> None:
    result = detect_indent(buf)
    if result is None:
        return
    use_spaces, size = result
    try:
        win = api.active_window()
        win.set_option("expandtab", use_spaces)
        win.set_option("tabstop", size)
        win.set_option("shiftwidth", size)
    except Exception:
        pass


def detect_indent(buf: Any) -> tuple[bool, int] | None:
    """
    Inspect leading whitespace of up to _SCAN_LINES lines.
    Returns (use_spaces, indent_size) or None if no indented lines found.
    """
    tab_count = 0
    space_counts: dict[int, int] = {}
    in_triple_quote = False

    limit = min(buf.line_count(), _SCAN_LINES)
    for i in range(limit):
        line = buf.get_line(i)

        if in_triple_quote:
            if '"""' in line or "'''" in line:
                in_triple_quote = False
            continue

        if not line:
            continue
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue

        # Skip lines containing triple-quote markers; track whether a string is opened.
        if '"""' in line or "'''" in line:
            dq = line.count('"""')
            sq = line.count("'''")
            if dq % 2 == 1 or sq % 2 == 1:
                in_triple_quote = True
            continue

        if line[0] == "\t":
            tab_count += 1
        elif line[0] == " ":
            n = len(line) - len(line.lstrip(" "))
            if n >= 2:
                space_counts[n] = space_counts.get(n, 0) + 1

    total_space = sum(space_counts.values())
    if tab_count == 0 and total_space == 0:
        return None
    if tab_count >= total_space:
        return (False, 4)
    return (True, _guess_size(space_counts))


def _guess_size(space_counts: dict[int, int]) -> int:
    """Return the most likely indent unit (2, 3, 4, or 8).

    Strategy: find the most frequently occurring indentation amount (above 10%
    frequency threshold), then snap it to the nearest standard candidate.
    Using the most frequent size rather than the minimum avoids being misled by
    minority noise such as 2-space lines inside module-level docstrings when the
    real indent unit is 4.

    Special case: if all qualifying sizes are multiples of 4 and the dominant
    size is >= 8, it most likely means multi-level 4-space indentation (e.g.
    only 8-space lines visible in the scan window), so prefer 4.
    """
    if not space_counts:
        return 4
    total = sum(space_counts.values())
    threshold = max(1, total * 0.10)
    candidates = [2, 3, 4, 8]
    qualifying = [(n, freq) for n, freq in sorted(space_counts.items()) if freq >= threshold]
    if not qualifying:
        return 4
    # Pick the most frequent qualifying size; ties resolved by smallest n (stable sort ascending).
    best_n = max(qualifying, key=lambda x: x[1])[0]
    # If only large multiples of 4 were found, the base unit is likely 4 — not 8.
    # Example: a file scanned only at its 2nd nesting level shows 8-space lines.
    if best_n >= 8 and all(n % 4 == 0 for n, _ in qualifying):
        return 4
    return min(candidates, key=lambda c: (abs(c - best_n), c))
