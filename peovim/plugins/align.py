"""
align — visual line alignment for characters and regexes.

Visual-mode bindings:
  ga  → prompt for a single alignment character via :AlignChar
  gA  → prompt for a regex via :AlignRegex
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

_pending_visual_range: tuple[int, int] | None = None
_pending_buf_id: int | None = None


def setup(api: EditorAPI) -> None:
    """Register the align plugin."""
    api.keymap.define_vplug("AlignCharPrompt", lambda ctx: _prompt_align_char(ctx, api), desc="Align: character")
    api.keymap.define_vplug("AlignRegexPrompt", lambda ctx: _prompt_align_regex(ctx, api), desc="Align: regex")
    api.keymap.vmap("ga", "<Plug>AlignCharPrompt", desc="Align: character")
    api.keymap.vmap("gA", "<Plug>AlignRegexPrompt", desc="Align: regex")
    api.commands.register("AlignChar", lambda cmd, ctx: _cmd_align_char(api, cmd.args), min_abbrev=6)
    api.commands.register("AlignRegex", lambda cmd, ctx: _cmd_align_regex(api, cmd.args), min_abbrev=6)


def teardown() -> None:
    _clear_pending_selection()


def _prompt_align_char(ctx: Any, api: Any) -> None:
    _remember_visual_selection(ctx, api)
    api.open_cmdline("AlignChar ")


def _prompt_align_regex(ctx: Any, api: Any) -> None:
    _remember_visual_selection(ctx, api)
    api.open_cmdline("AlignRegex ")


def _remember_visual_selection(ctx: Any, api: Any) -> None:
    global _pending_visual_range, _pending_buf_id

    visual_range = getattr(ctx, "visual_range", None)
    if visual_range is None:
        line = api.active_window().cursor[0]
        visual_range = (line, line)
    _pending_visual_range = visual_range
    _pending_buf_id = api.active_buffer().buf_id


def _clear_pending_selection() -> None:
    global _pending_visual_range, _pending_buf_id
    _pending_visual_range = None
    _pending_buf_id = None


def _consume_line_range(api: Any) -> tuple[Any, int, int]:
    buf = api.active_buffer()
    active_range = _pending_visual_range if _pending_buf_id == buf.buf_id else None
    _clear_pending_selection()
    if active_range is not None:
        return buf, active_range[0], active_range[1]
    line = api.active_window().cursor[0]
    return buf, line, line


def _cmd_align_char(api: Any, raw_args: str) -> None:
    target = _parse_char_argument(raw_args)
    if target is None:
        _clear_pending_selection()
        _set_status(api, "AlignChar expects a target character")
        return
    _align(api, lambda line: line.find(target))


def _cmd_align_regex(api: Any, raw_args: str) -> None:
    pattern = raw_args.strip()
    if not pattern:
        _clear_pending_selection()
        _set_status(api, "AlignRegex expects a pattern")
        return
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        _clear_pending_selection()
        _set_status(api, f"Invalid regex: {exc}")
        return
    _align(api, lambda line: _match_start(compiled, line))


def _align(api: Any, locate_column) -> None:
    buf, start_line, end_line = _consume_line_range(api)
    lines = [buf.get_line(line) for line in range(start_line, end_line + 1)]
    positions = [locate_column(line) for line in lines]
    valid_positions = [position for position in positions if position is not None and position >= 0]
    if not valid_positions:
        _set_status(api, "No alignment matches in selection")
        return

    target_col = max(valid_positions)
    with buf.batch():
        for offset, (line_text, position) in enumerate(zip(lines, positions, strict=False)):
            if position is None or position < 0 or position == target_col:
                continue
            padding = " " * (target_col - position)
            new_text = line_text[:position] + padding + line_text[position:]
            line_no = start_line + offset
            buf.replace(line_no, 0, line_no, len(line_text), new_text)


def _match_start(compiled: re.Pattern[str], text: str) -> int | None:
    match = compiled.search(text)
    if match is None:
        return None
    return match.start()


def _parse_char_argument(raw_args: str) -> str | None:
    value = raw_args.strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered == "space":
        return " "
    if lowered == "tab":
        return "\t"
    return value[0]


def _set_status(api: Any, message: str) -> None:
    api.set_status(message)
