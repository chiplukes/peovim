"""
commands.builtin — All standard Vim ex command handlers

Registers handlers for: :w/:write, :q/:quit, :wq/:x, :e/:edit,
:set, :s/:substitute, :d/:delete, :y/:yank, :split/:vsplit, :normal,
:echo, :map/:nmap/:imap/:unmap.
"""

from __future__ import annotations

import re
from typing import Any

from peovim.commands.parser import ParsedCommand
from peovim.commands.registry import CommandRegistry
from peovim.config.loader import preferred_user_config_path
from peovim.core.style import Style
from peovim.modal.actions import (
    CloseWindow,
    DeleteRange,
    NewTab,
    NextTab,
    OnlyWindow,
    OpenBuffer,
    PrevTab,
    QuitEditor,
    RunNormalKeys,
    SaveBuffer,
    SplitWindow,
    TabClose,
    YankLine,
)

_PALETTE_NAMESPACE = "cmd:palette"
_REFERENCE_PALETTE = (
    (
        "Neutrals",
        (
            (
                "charcoal",
                (
                    (12, 12, 12),
                    (22, 22, 22),
                    (30, 30, 30),
                    (45, 45, 48),
                    (62, 62, 66),
                    (96, 96, 96),
                    (140, 140, 140),
                    (212, 212, 212),
                ),
            ),
            (
                "slate",
                (
                    (18, 24, 30),
                    (28, 36, 44),
                    (39, 49, 60),
                    (55, 69, 84),
                    (77, 95, 115),
                    (110, 132, 156),
                    (156, 181, 207),
                    (214, 227, 240),
                ),
            ),
            (
                "warm gray",
                (
                    (24, 20, 18),
                    (38, 32, 29),
                    (52, 46, 42),
                    (72, 64, 58),
                    (98, 88, 81),
                    (132, 121, 112),
                    (177, 167, 159),
                    (226, 219, 213),
                ),
            ),
        ),
    ),
    (
        "Muted tones",
        (
            (
                "brick",
                (
                    (58, 26, 26),
                    (82, 36, 36),
                    (110, 48, 48),
                    (145, 63, 63),
                    (182, 84, 84),
                    (212, 112, 112),
                    (233, 146, 146),
                    (247, 188, 188),
                ),
            ),
            (
                "rust",
                (
                    (67, 35, 17),
                    (96, 49, 23),
                    (128, 67, 31),
                    (164, 89, 41),
                    (198, 116, 55),
                    (224, 145, 77),
                    (241, 180, 113),
                    (250, 214, 162),
                ),
            ),
            (
                "olive",
                (
                    (54, 55, 20),
                    (76, 78, 28),
                    (102, 104, 38),
                    (131, 134, 51),
                    (162, 166, 68),
                    (194, 198, 92),
                    (218, 220, 128),
                    (236, 238, 173),
                ),
            ),
            (
                "moss",
                (
                    (28, 52, 24),
                    (40, 74, 34),
                    (55, 100, 47),
                    (74, 128, 63),
                    (97, 158, 82),
                    (125, 188, 108),
                    (160, 214, 144),
                    (202, 235, 190),
                ),
            ),
            (
                "teal",
                (
                    (18, 55, 57),
                    (25, 78, 81),
                    (36, 106, 110),
                    (49, 138, 142),
                    (67, 171, 175),
                    (95, 201, 203),
                    (134, 224, 225),
                    (186, 240, 240),
                ),
            ),
            (
                "steel blue",
                (
                    (28, 42, 68),
                    (39, 59, 94),
                    (54, 80, 127),
                    (72, 104, 162),
                    (96, 131, 194),
                    (127, 162, 218),
                    (166, 195, 235),
                    (209, 226, 246),
                ),
            ),
            (
                "plum",
                (
                    (49, 28, 60),
                    (69, 39, 84),
                    (93, 53, 113),
                    (121, 72, 145),
                    (152, 97, 177),
                    (185, 128, 206),
                    (214, 167, 228),
                    (236, 209, 244),
                ),
            ),
        ),
    ),
    (
        "Saturated tones",
        (
            (
                "crimson",
                (
                    (72, 0, 16),
                    (108, 0, 24),
                    (150, 0, 34),
                    (196, 0, 45),
                    (224, 32, 70),
                    (241, 78, 107),
                    (249, 131, 154),
                    (253, 189, 202),
                ),
            ),
            (
                "amber",
                (
                    (72, 36, 0),
                    (108, 54, 0),
                    (152, 76, 0),
                    (198, 100, 0),
                    (229, 132, 0),
                    (245, 168, 48),
                    (252, 201, 103),
                    (255, 227, 166),
                ),
            ),
            (
                "gold",
                (
                    (74, 60, 0),
                    (112, 91, 0),
                    (156, 127, 0),
                    (204, 167, 0),
                    (233, 198, 36),
                    (246, 220, 90),
                    (252, 236, 144),
                    (255, 247, 196),
                ),
            ),
            (
                "emerald",
                (
                    (0, 54, 24),
                    (0, 84, 37),
                    (0, 118, 52),
                    (0, 156, 69),
                    (0, 194, 94),
                    (44, 220, 125),
                    (109, 237, 166),
                    (181, 248, 209),
                ),
            ),
            (
                "turquoise",
                (
                    (0, 53, 53),
                    (0, 82, 82),
                    (0, 117, 117),
                    (0, 158, 158),
                    (0, 200, 200),
                    (54, 224, 224),
                    (121, 239, 239),
                    (190, 248, 248),
                ),
            ),
            (
                "azure",
                (
                    (0, 32, 72),
                    (0, 49, 110),
                    (0, 69, 156),
                    (0, 92, 208),
                    (36, 124, 236),
                    (94, 162, 247),
                    (154, 200, 252),
                    (208, 230, 255),
                ),
            ),
            (
                "violet",
                (
                    (44, 0, 68),
                    (69, 0, 104),
                    (98, 0, 150),
                    (132, 0, 198),
                    (166, 43, 225),
                    (196, 100, 238),
                    (220, 157, 246),
                    (239, 209, 251),
                ),
            ),
            (
                "magenta",
                (
                    (72, 0, 52),
                    (110, 0, 79),
                    (156, 0, 112),
                    (208, 0, 149),
                    (234, 46, 176),
                    (247, 100, 198),
                    (252, 156, 219),
                    (255, 210, 238),
                ),
            ),
        ),
    ),
    (
        "Pure hues",
        (
            (
                "pure red",
                ((64, 0, 0), (128, 0, 0), (192, 0, 0), (255, 0, 0), (255, 64, 64), (255, 128, 128), (255, 192, 192)),
            ),
            (
                "pure orange",
                (
                    (64, 24, 0),
                    (128, 48, 0),
                    (192, 96, 0),
                    (255, 128, 0),
                    (255, 160, 64),
                    (255, 200, 128),
                    (255, 228, 192),
                ),
            ),
            (
                "pure yellow",
                (
                    (64, 64, 0),
                    (128, 128, 0),
                    (192, 192, 0),
                    (255, 255, 0),
                    (255, 255, 96),
                    (255, 255, 160),
                    (255, 255, 210),
                ),
            ),
            (
                "pure green",
                ((0, 48, 0), (0, 96, 0), (0, 160, 0), (0, 255, 0), (96, 255, 96), (160, 255, 160), (215, 255, 215)),
            ),
            (
                "pure cyan",
                (
                    (0, 48, 48),
                    (0, 96, 96),
                    (0, 176, 176),
                    (0, 255, 255),
                    (96, 255, 255),
                    (170, 255, 255),
                    (225, 255, 255),
                ),
            ),
            (
                "pure blue",
                ((0, 0, 64), (0, 0, 128), (0, 0, 192), (0, 0, 255), (96, 96, 255), (160, 160, 255), (214, 214, 255)),
            ),
            (
                "pure magenta",
                (
                    (64, 0, 64),
                    (128, 0, 128),
                    (192, 0, 192),
                    (255, 0, 255),
                    (255, 96, 255),
                    (255, 160, 255),
                    (255, 220, 255),
                ),
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Context protocol
# ---------------------------------------------------------------------------
# The context passed to handlers is expected to have:
#   context.window       — Window (with .document, .cursor)
#   context.engine       — ModalEngine
#   context.dispatcher   — ActionDispatcher
#   context.registers    — RegisterStore
# All attributes are optional — handlers degrade gracefully if missing.


def _get_window(ctx: Any):
    return getattr(ctx, "window", None)


def _get_doc(ctx: Any):
    w = _get_window(ctx)
    return w.document if w else None


def _get_cursor(ctx: Any):
    w = _get_window(ctx)
    return w.cursor if w else None


def _get_engine(ctx: Any):
    return getattr(ctx, "engine", None)


def _get_dispatcher(ctx: Any):
    return getattr(ctx, "dispatcher", None)


def _set_window_option(ctx: Any, key: str, value: Any) -> None:
    window = _get_window(ctx)
    if window is None:
        return
    window.options[key] = value
    if key == "fileformat":
        window.document.set_fileformat(value)


def _resolve_range(cmd: ParsedCommand, ctx: Any) -> tuple[int, int]:
    """
    Resolve a command's range to (start_line, end_line).
    Returns (cursor_line, cursor_line) if no range given.
    """
    cursor = _get_cursor(ctx)
    doc = _get_doc(ctx)
    cur_line = cursor.line if cursor else 0
    total = doc.line_count() if doc else 1

    if cmd.all_lines:
        return (0, total - 1)

    def resolve_addr(addr):
        if addr is None:
            return cur_line
        if addr.kind == "line":
            return max(0, min(int(addr.value) - 1, total - 1)) + addr.offset
        if addr.kind == "dot":
            return cur_line + addr.offset
        if addr.kind == "dollar":
            return (total - 1) + addr.offset
        if addr.kind == "mark":
            mark_name = addr.value
            # '< and '> resolve from the last visual selection
            if mark_name in ("<", ">"):
                engine = getattr(ctx, "engine", None)
                last_sel = getattr(engine, "_last_visual_selection", None) if engine else None
                if last_sel is not None:
                    _, anchor, cursor_pos = last_sel
                    line = min(anchor[0], cursor_pos[0]) if mark_name == "<" else max(anchor[0], cursor_pos[0])
                    return line + addr.offset
            # Other named marks
            marks = getattr(ctx, "marks", None)
            if marks is not None:
                pos = marks.get(mark_name)
                if pos is not None:
                    return pos[0] + addr.offset
        return cur_line

    start = resolve_addr(cmd.range_start)
    end = resolve_addr(cmd.range_end) if cmd.range_end is not None else start
    return (max(0, start), max(0, end))


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _cmd_write(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([SaveBuffer(force=cmd.bang, path=cmd.args or None)])


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


def _cmd_quit(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([QuitEditor(force=cmd.bang)])


# ---------------------------------------------------------------------------
# Write + quit
# ---------------------------------------------------------------------------


def _cmd_wq(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([SaveBuffer(), QuitEditor()])


# ---------------------------------------------------------------------------
# Edit (open file)
# ---------------------------------------------------------------------------


def _cmd_edit(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    doc = _get_doc(ctx)
    win = _get_window(ctx)
    if disp and cmd.args:
        disp.dispatch([OpenBuffer(cmd.args)])
        return

    if doc is None or win is None:
        return
    if doc.path is None:
        _set_message(ctx, "E32: No file name")
        return
    if doc.dirty and not cmd.bang:
        _set_message(ctx, "E37: No write since last change (add ! to override)")
        return

    original_line = win.cursor.line
    original_col = win.cursor.col
    doc.reload()
    win.options["fileformat"] = doc.fileformat
    max_line = max(0, doc.line_count() - 1)
    target_line = min(original_line, max_line)
    target_col = min(original_col, max(0, len(doc.get_line(target_line)) - 1))
    win.cursor.move_to(target_line, target_col)
    win.scroll_to_cursor()
    if doc.had_mixed_line_endings:
        _set_message(
            ctx,
            f"Mixed line endings detected: {doc.path} (saving will normalize to {doc.fileformat})",
        )
        return
    _set_message(ctx, f"Reloaded: {doc.path}")


def _open_user_config(ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp is None:
        return

    config_path = preferred_user_config_path().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("", encoding="utf-8")
    disp.dispatch([OpenBuffer(str(config_path))])


def _cmd_config(cmd: ParsedCommand, ctx: Any) -> None:
    _open_user_config(ctx)


# ---------------------------------------------------------------------------
# Close window
# ---------------------------------------------------------------------------


def _cmd_close(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([CloseWindow(force=cmd.bang)])


# ---------------------------------------------------------------------------
# Delete lines
# ---------------------------------------------------------------------------


def _cmd_delete(cmd: ParsedCommand, ctx: Any) -> None:
    start, end = _resolve_range(cmd, ctx)
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([DeleteRange(start, 0, end, 0x7FFFFFFF)])


# ---------------------------------------------------------------------------
# Yank lines
# ---------------------------------------------------------------------------


def _cmd_yank(cmd: ParsedCommand, ctx: Any) -> None:
    start, end = _resolve_range(cmd, ctx)
    register = cmd.args.strip() or '"'
    count = end - start + 1
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([YankLine(start, count, register)])


# ---------------------------------------------------------------------------
# Substitute: s/pattern/replacement/flags
# ---------------------------------------------------------------------------


def _cmd_substitute(cmd: ParsedCommand, ctx: Any) -> None:
    doc = _get_doc(ctx)
    cursor = _get_cursor(ctx)
    if not doc or not cursor:
        return

    args = cmd.args
    if not args:
        return

    sep = args[0]
    parts = args[1:].split(sep)
    if len(parts) < 2:
        return

    pattern = parts[0]
    replacement = parts[1]
    flags_str = parts[2] if len(parts) > 2 else ""

    # Reuse last search pattern if pattern is empty
    es = getattr(ctx, "editor_state", None)
    if not pattern and es is not None and es.search.pattern:
        pattern = es.search.pattern

    if not pattern:
        return

    count_flag = "g" in flags_str
    confirm_flag = "c" in flags_str
    ignore_case = "i" in flags_str

    start, end = _resolve_range(cmd, ctx)

    try:
        re_flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(pattern, re_flags)

        if confirm_flag and es is not None:
            # Collect all matches — apply interactively via event loop confirm state
            from peovim.core.editor_state import ConfirmSubState

            matches: list[tuple[int, int, int, str]] = []
            for ln in range(start, end + 1):
                if ln >= doc.line_count():
                    break
                text = doc.get_line(ln)
                search_from = 0
                while True:
                    m = compiled.search(text, search_from)
                    if m is None:
                        break
                    try:
                        rep_text = m.expand(replacement)
                    except re.error:
                        rep_text = replacement
                    matches.append((ln, m.start(), m.end(), rep_text))
                    if not count_flag:
                        break  # only first match per line without g
                    search_from = m.end()
                    if search_from >= len(text):
                        break
            if matches:
                es.confirm_sub = ConfirmSubState(matches, replacement)
                es.search.set_pattern(pattern, "forward")
            return

        changed = False
        for ln in range(start, end + 1):
            if ln >= doc.line_count():
                break
            text = doc.get_line(ln)
            new_text = compiled.sub(replacement, text) if count_flag else compiled.sub(replacement, text, count=1)
            if new_text != text:
                changed = True
                col_end = len(text)
                doc.delete(ln, 0, ln, col_end)
                if new_text:
                    doc.insert(ln, 0, new_text)
        # Store pattern in editor_state for reuse and hlsearch
        if changed and es is not None:
            es.search.set_pattern(pattern, "forward")
    except re.error:
        pass  # silently ignore bad patterns in headless


# ---------------------------------------------------------------------------
# Bang filter command
# ---------------------------------------------------------------------------


def _cmd_bang(cmd: ParsedCommand, ctx: Any) -> None:
    """:{range}!{cmd} — filter range through shell command."""
    from peovim.modal.actions import FilterRange

    disp = _get_dispatcher(ctx)
    if disp and cmd.args:
        start, end = _resolve_range(cmd, ctx)
        disp.dispatch([FilterRange(start, end, cmd.args)])


# ---------------------------------------------------------------------------
# Set option
# ---------------------------------------------------------------------------


def _cmd_set(cmd: ParsedCommand, ctx: Any) -> None:
    import contextlib

    window = _get_window(ctx)
    if not window or not cmd.args:
        return
    es = getattr(ctx, "editor_state", None)
    opts_store = getattr(es, "options", None) if es else None
    args = cmd.args.split()
    for arg in args:
        if "=" in arg:
            key, _, val = arg.partition("=")
            try:
                typed_val: Any = int(val)
            except ValueError:
                typed_val = val
            _set_window_option(ctx, key, typed_val)
            if opts_store is not None and opts_store.is_known(key):
                with contextlib.suppress(Exception):
                    opts_store.set_global(key, typed_val)
        elif arg.startswith("no"):
            key = arg[2:]
            _set_window_option(ctx, key, False)
            if opts_store is not None and opts_store.is_known(key):
                with contextlib.suppress(Exception):
                    opts_store.set_global(key, False)
        else:
            _set_window_option(ctx, arg, True)
            if opts_store is not None and opts_store.is_known(arg):
                with contextlib.suppress(Exception):
                    opts_store.set_global(arg, True)


def _cmd_dos2unix(cmd: ParsedCommand, ctx: Any) -> None:
    """Convert all line endings to LF and set fileformat=unix."""
    doc = _get_doc(ctx)
    if doc is None:
        return
    text = doc.get_text()
    # Strip any \r\n or lone \r that may be in the buffer (e.g. pasted content)
    converted = text.replace("\r\n", "\n").replace("\r", "\n")
    if converted != text:
        line_count = doc.line_count()
        last_line = max(0, line_count - 1)
        last_col = len(doc.get_line(last_line))
        doc.replace(0, 0, last_line, last_col, converted)
        _set_message(ctx, "dos2unix: converted line endings to LF")
    else:
        _set_message(ctx, "dos2unix: line endings already LF")
    _set_window_option(ctx, "fileformat", "unix")


def _cmd_unix2dos(cmd: ParsedCommand, ctx: Any) -> None:
    """Convert all line endings to CRLF and set fileformat=dos."""
    doc = _get_doc(ctx)
    if doc is None:
        return
    # Buffer stores \n internally; unix2dos marks file to save with \r\n.
    # No content change needed — fileformat controls the on-disk encoding.
    _set_window_option(ctx, "fileformat", "dos")
    _set_message(ctx, "unix2dos: file will save with CRLF line endings")


# ---------------------------------------------------------------------------
# Normal (run normal-mode keys)
# ---------------------------------------------------------------------------


def _cmd_tabs2spaces(cmd: ParsedCommand, ctx: Any) -> None:
    """Convert all tab characters in the buffer to spaces using the current tabstop."""
    doc = _get_doc(ctx)
    window = _get_window(ctx)
    if doc is None or window is None:
        return
    tabstop = int(window.options.get("tabstop") or 4)
    text = doc.get_text()
    if "\t" not in text:
        _set_message(ctx, "tabs2spaces: no tab characters found")
        return
    normalized = text.expandtabs(tabstop)
    if normalized == text:
        _set_message(ctx, "tabs2spaces: no changes")
        return
    line_count = doc.line_count()
    last_line = max(0, line_count - 1)
    last_col = len(doc.get_line(last_line)) if line_count > 0 else 0
    doc.replace(0, 0, last_line, last_col, normalized)
    _set_window_option(ctx, "expandtab", True)
    _set_message(ctx, f"tabs2spaces: converted tabs to spaces (tabstop={tabstop})")


def _cmd_normal(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp and cmd.args:
        disp.dispatch([RunNormalKeys(cmd.args, remap=not cmd.bang)])


# ---------------------------------------------------------------------------
# Echo
# ---------------------------------------------------------------------------


def _cmd_echo(cmd: ParsedCommand, ctx: Any) -> Any:
    return cmd.args  # caller decides what to do with this


# ---------------------------------------------------------------------------
# No-highlight search
# ---------------------------------------------------------------------------


def _cmd_nohlsearch(cmd: ParsedCommand, ctx: Any) -> None:
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.search.hlsearch_active = False


# ---------------------------------------------------------------------------
# colorscheme
# ---------------------------------------------------------------------------


def _cmd_colorscheme(cmd: ParsedCommand, ctx: Any) -> None:
    name = cmd.args.strip()
    if not name:
        es = getattr(ctx, "editor_state", None)
        current = getattr(es, "active_theme", "catppuccin") if es else "catppuccin"
        _set_message(ctx, current)
        return
    from peovim.syntax.themes import get_theme

    if get_theme(name) is None:
        _set_message(ctx, f"E185: Cannot find color scheme '{name}'")
        return
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.active_theme = name


def _rgb_to_hex(color: tuple[int, int, int] | None) -> str:
    if color is None:
        return "default"
    red, green, blue = color
    return f"#{red:02X}{green:02X}{blue:02X}"


def _rgb_to_text(color: tuple[int, int, int] | None) -> str:
    if color is None:
        return "terminal default"
    red, green, blue = color
    return f"rgb({red:3d},{green:3d},{blue:3d})"


def _contrast_fg(color: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = color
    luma = (299 * red + 587 * green + 114 * blue) / 1000
    return (0, 0, 0) if luma >= 150 else (255, 255, 255)


def _build_palette_view(theme_name: str, theme: Any) -> tuple[str, list[tuple[int, int, int, Style]]]:
    theme_bg = _rgb_to_hex(theme.default_bg)
    theme_bg_text = _rgb_to_text(theme.default_bg)
    lines = [
        f"Palette preview for theme: {theme_name}",
        "Edit RGB values, reload config, then run :palette again. Use :bd to return.",
        f"Theme default_bg: {theme_bg} {theme_bg_text}",
        "Terminal default bg appears only when a theme/group background is None.",
        "",
        "Theme groups",
    ]
    highlights: list[tuple[int, int, int, Style]] = []

    entries: list[tuple[str, str, tuple[int, int, int] | None]] = [
        ("default", "fg", theme.default_fg),
        ("default", "bg", theme.default_bg),
    ]
    for group, style in sorted(theme.groups.items()):
        if style.fg is not None:
            entries.append((group, "fg", style.fg))
        if style.bg is not None:
            entries.append((group, "bg", style.bg))

    for group, role, color in entries:
        prefix = f"{group:<24} {role:<2} {_rgb_to_hex(color):<8} {_rgb_to_text(color):<18} "
        if color is None:
            lines.append(prefix + "(uses terminal default)")
            continue

        fg_sample = "sample text"
        bg_sample = " #RRGGBB "
        line = prefix + fg_sample + "  " + bg_sample
        line_index = len(lines)
        fg_start = len(prefix)
        bg_start = fg_start + len(fg_sample) + 2
        lines.append(line)
        highlights.append((line_index, fg_start, fg_start + len(fg_sample), Style(fg=color)))
        highlights.append(
            (
                line_index,
                bg_start,
                bg_start + len(bg_sample),
                Style(fg=_contrast_fg(color), bg=color),
            )
        )

    lines.extend(["", "Reference palette", "Compare neutral, muted, saturated, and pure hues."])
    for section_name, ramps in _REFERENCE_PALETTE:
        lines.extend(["", section_name])
        for ramp_name, colors in ramps:
            line = f"{ramp_name:<16}"
            blocks: list[tuple[int, int, tuple[int, int, int]]] = []
            for color in colors:
                block = f" {_rgb_to_hex(color)} "
                start = len(line)
                line += block
                blocks.append((start, len(line), color))
            line_index = len(lines)
            lines.append(line)
            for start, end, color in blocks:
                highlights.append((line_index, start, end, Style(fg=_contrast_fg(color), bg=color)))

    return "\n".join(lines), highlights


def _cmd_palette(cmd: ParsedCommand, ctx: Any) -> None:
    from peovim.syntax.themes import get_theme

    es = getattr(ctx, "editor_state", None)
    current_doc = _get_doc(ctx)
    if es is None or current_doc is None:
        return

    theme_name = cmd.args.strip() or getattr(es, "active_theme", "catppuccin")
    theme = get_theme(theme_name)
    if theme is None:
        _set_message(ctx, f"E185: Cannot find color scheme '{theme_name}'")
        return

    text, highlights = _build_palette_view(theme_name, theme)
    es.decorations.clear_namespace(id(current_doc), _PALETTE_NAMESPACE)
    doc = _load_scratch_text(ctx, text)
    if doc is None:
        return
    doc.filetype = ""

    for line, start_col, end_col, style in highlights:
        es.decorations.add(
            id(doc),
            _PALETTE_NAMESPACE,
            __import__("peovim.ui.decorations", fromlist=["HighlightRegion"]).HighlightRegion(
                line, start_col, line, end_col, style, priority=60
            ),
        )
    _set_message(ctx, f"Palette: {theme_name}")


def _set_message(ctx: Any, msg: str) -> None:
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.message = msg


def _load_scratch_text(ctx: Any, text: str) -> Any:
    """Replace the current window buffer with scratch text and reset cursor/scroll."""
    from peovim.core.document import Document

    doc = _get_doc(ctx)
    win = _get_window(ctx)
    es = getattr(ctx, "editor_state", None)
    if doc is None or win is None:
        return None

    if es is not None and doc.path is not None:
        es.alt_path = str(doc.path)
        es.alt_cursor = (win.cursor.line, win.cursor.col)

    scratch_doc = Document()
    scratch_doc.load_string(text)
    scratch_doc.path = None
    win.document = scratch_doc

    workspace = getattr(ctx, "workspace", None)
    if workspace is not None:
        workspace.add_document(scratch_doc)

    win.options["fileformat"] = scratch_doc.fileformat
    win.cursor.move_to(0, 0)
    win.scroll_line = 0
    win.scroll_col = 0
    return scratch_doc


# ---------------------------------------------------------------------------
# Map commands (no-op stubs — real implementation lives in plugin API)
# ---------------------------------------------------------------------------


def _cmd_map(cmd: ParsedCommand, ctx: Any) -> None:
    pass  # handled by plugin API


# ---------------------------------------------------------------------------
# Checkhealth
# ---------------------------------------------------------------------------


def _cmd_checkhealth(cmd: ParsedCommand, ctx: Any) -> None:
    """Run health checks and load report into current buffer."""
    disp = _get_dispatcher(ctx)
    doc = _get_doc(ctx)
    win = _get_window(ctx)
    if disp is None or doc is None or win is None:
        return

    # Retrieve EditorAPI through editor_state if available
    es = getattr(ctx, "editor_state", None)
    api = getattr(es, "_api", None) if es is not None else None

    if api is None:
        _set_message(ctx, "[checkhealth] EditorAPI not available")
        return

    try:
        report = api.health.run(api)
    except Exception as exc:
        _set_message(ctx, f"[checkhealth] error: {exc}")
        return

    scratch_doc = _load_scratch_text(ctx, report)
    _apply_checkhealth_highlights(report, scratch_doc, es)


def _apply_checkhealth_highlights(report: str, scratch_doc: Any, es: Any) -> None:
    """Apply colour decorations to a loaded :checkhealth scratch buffer."""
    if scratch_doc is None or es is None:
        return
    decorations = getattr(es, "decorations", None)
    if decorations is None:
        return

    from peovim.core.health import highlight_report
    from peovim.ui.decorations import HighlightRegion

    buf_id = id(scratch_doc)
    ns = "checkhealth"
    decorations.clear_namespace(buf_id, ns)

    for line, col_start, col_end, style in highlight_report(report):
        if col_start < col_end:
            decorations.add(buf_id, ns, HighlightRegion(line, col_start, line, col_end, style))


def _cmd_messages(cmd: ParsedCommand, ctx: Any) -> None:
    es = getattr(ctx, "editor_state", None)
    if es is None:
        return
    lines = es.message_history or ["(no messages)"]
    _load_scratch_text(ctx, "\n".join(lines))


# ---------------------------------------------------------------------------
# Buffer delete / close (:bd)
# ---------------------------------------------------------------------------


def _cmd_bdelete(cmd: ParsedCommand, ctx: Any) -> None:
    """Close the current scratch buffer, returning to the alternate file if available."""
    es = getattr(ctx, "editor_state", None)
    disp = _get_dispatcher(ctx)
    api = getattr(es, "_api", None) if es is not None else None
    doc = _get_doc(ctx)
    win = _get_window(ctx)
    if doc is None or win is None:
        return

    win_count = api.window_count() if api is not None else 0
    if doc.path is None and win_count > 1 and disp is not None:
        disp.dispatch([CloseWindow()])
        return

    alt = getattr(es, "alt_path", None) if es is not None else None
    if alt:
        # Return to the previous file and restore cursor position
        alt_cursor = getattr(es, "alt_cursor", (0, 0)) if es is not None else (0, 0)
        if disp is not None:
            disp.dispatch([OpenBuffer(alt)])
            # Restore cursor to where it was before the jump
            if win is not None:
                win.cursor.move_to(alt_cursor[0], alt_cursor[1])
                win.scroll_to_cursor()
        if es is not None:
            es.alt_path = None
            es.alt_cursor = (0, 0)
    else:
        # No alternate file — just clear to empty scratch buffer
        line_count = doc.line_count()
        if line_count > 0:
            last_line_len = len(doc.get_line(line_count - 1))
            doc.delete(0, 0, line_count - 1, last_line_len)
        doc.path = None
        doc.mark_clean()
        win.cursor.move_to(0, 0)
        win.scroll_line = 0


# ---------------------------------------------------------------------------
# Window / split / tab ex commands
# ---------------------------------------------------------------------------


def _cmd_split(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        path = cmd.args.strip() or None
        disp.dispatch([SplitWindow("h", buffer_path=path)])


def _cmd_vsplit(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        path = cmd.args.strip() or None
        disp.dispatch([SplitWindow("v", buffer_path=path)])


def _cmd_only(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([OnlyWindow()])


def _cmd_tabnew(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([NewTab()])


def _cmd_tabnext(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([NextTab()])


def _cmd_tabprev(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([PrevTab()])


def _cmd_tabclose(cmd: ParsedCommand, ctx: Any) -> None:
    disp = _get_dispatcher(ctx)
    if disp:
        disp.dispatch([TabClose(force=cmd.bang)])


# ---------------------------------------------------------------------------
# Log commands
# ---------------------------------------------------------------------------


def _parse_log_args(args: str) -> tuple[list[str] | None, str, bool]:
    """
    Parse `:LogOn` args string into (modules, level, write_file).

    Syntax: [modules=<patterns>] [level=<level>] [file=no]
      modules: comma-separated patterns, each optionally with :level suffix
               "peovim.core.*"  or  "peovim.core.*:debug,peovim.ui:info"
      level:   debug / info / warning / error  (global fallback)
      file=no: disable file logging (ring buffer only)

    Returns (modules_list_or_None, level_str, write_file).
    """
    import re

    modules: list[str] | None = None
    level = "debug"
    write_file = True

    for m in re.finditer(r"(\w+)=(\S+)", args):
        key, val = m.group(1).lower(), m.group(2)
        if key == "modules":
            modules = [p.strip() for p in val.split(",") if p.strip()]
        elif key == "level":
            level = val
        elif key == "file" and val.lower() in ("no", "off", "false", "0"):
            write_file = False

    return modules, level, write_file


def _cmd_logon(cmd: ParsedCommand, ctx: Any) -> None:
    from peovim.core.log_manager import get_log_manager

    modules, level, write_file = _parse_log_args(cmd.args)
    mgr = get_log_manager()
    log_path = mgr.enable(modules=modules, level=level, write_file=write_file)
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        if log_path:
            desc = f"modules={modules or 'all'}  level={level}"
            es.message = f"LogOn: {desc} → {log_path}"
        else:
            es.message = f"LogOn: modules={modules or 'all'}  level={level} (ring buffer only)"


def _cmd_logoff(cmd: ParsedCommand, ctx: Any) -> None:
    from peovim.core.log_manager import get_log_manager

    get_log_manager().disable()
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.message = "LogOff: logging disabled"


def _cmd_loglevel(cmd: ParsedCommand, ctx: Any) -> None:
    """`:LogLevel <level> [module=<name>]` — adjust level without restarting."""
    import re

    from peovim.core.log_manager import get_log_manager

    args = cmd.args.strip()
    module = "peovim"
    level = "debug"
    # Extract optional module= kwarg
    m = re.search(r"module=(\S+)", args)
    if m:
        module = m.group(1)
        args = args[: m.start()].strip()
    if args:
        level = args.split()[0]
    get_log_manager().set_level(level, module)
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.message = f"LogLevel: {module} → {level}"


def _cmd_logview(cmd: ParsedCommand, ctx: Any) -> None:
    """:LogView [last=N] — write ring buffer to temp file and open in a split."""
    import re

    from peovim.core.log_manager import get_log_manager

    last_n = 500
    m = re.search(r"last=(\d+)", cmd.args)
    if m:
        last_n = int(m.group(1))

    mgr = get_log_manager()
    lines = mgr.get_log_lines(last_n)

    es = getattr(ctx, "editor_state", None)
    if not lines:
        if es is not None:
            es.message = "LogView: no log entries (use :LogOn first)"
        return

    # Write to a temp file so we can open it as a normal buffer.
    try:
        import pathlib

        log_dir = pathlib.Path.home() / ".config" / "peovim"
        log_dir.mkdir(parents=True, exist_ok=True)
        view_path = str(log_dir / "peovim_logview.txt")

        pathlib.Path(view_path).write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        if es is not None:
            es.message = f"LogView error: {exc}"
        return

    from peovim.modal.actions import OpenBuffer, SplitWindow

    disp = _get_dispatcher(ctx)
    if disp is None:
        return
    disp.dispatch([SplitWindow("h"), OpenBuffer(view_path)])


def _cmd_logclear(cmd: ParsedCommand, ctx: Any) -> None:
    from peovim.core.log_manager import get_log_manager

    get_log_manager().clear()
    es = getattr(ctx, "editor_state", None)
    if es is not None:
        es.message = "LogClear: ring buffer cleared"


def _cmd_recoverfile(cmd: ParsedCommand, ctx: Any) -> None:
    """:RecoverFile [path] — restore buffer content from a crash-recovery snapshot."""
    import pathlib

    es = getattr(ctx, "editor_state", None)
    recovery_store = getattr(es, "recovery_store", None) if es is not None else None

    if recovery_store is None:
        if es is not None:
            es.message = "RecoverFile: no recovery store available"
        return

    # Determine target path
    path_arg = cmd.args.strip()
    target: pathlib.Path | None
    if path_arg:
        target = pathlib.Path(path_arg).resolve()
    else:
        doc = _get_doc(ctx)
        target = doc.path if doc is not None else None
    if target is None:
        if es is not None:
            es.message = "RecoverFile: no path — specify a path or open the file first"
        return

    if not recovery_store.exists_for_path(target):
        if es is not None:
            es.message = f"RecoverFile: no recovery file for {target}"
        return

    try:
        text = recovery_store.read(target)
    except Exception as exc:
        if es is not None:
            es.message = f"RecoverFile: read error: {exc}"
        return

    # Find the document for this path in the workspace and replace its content
    workspace = getattr(ctx, "workspace", None)
    doc = None
    if workspace is not None:
        doc = workspace.find_document_by_path(target)
    if doc is None:
        doc = _get_doc(ctx)
        if doc is None or doc.path != target:
            doc = None

    if doc is None:
        if es is not None:
            es.message = f"RecoverFile: {target.name} is not open — open it first with :e {target}"
        return

    doc.load_string(text)
    # Mark dirty so the user knows they need to :w to persist the recovered content
    doc._change_counter += 1
    recovery_store.delete(target)
    if es is not None:
        es.message = f"Recovered {target.name} — {len(text.splitlines())} lines restored (use :w to save)"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_builtins(registry: CommandRegistry) -> None:  # cm:2e7d3b
    """Register all built-in ex commands into the registry."""
    registry.register("write", _cmd_write, min_abbrev=1)
    registry.register("quit", _cmd_quit, min_abbrev=1)
    registry.register("wq", _cmd_wq, min_abbrev=2)
    registry.register("xit", _cmd_wq, min_abbrev=1)
    registry.register("edit", _cmd_edit, min_abbrev=1)
    registry.register("config", _cmd_config, min_abbrev=3)
    registry.register("init", _cmd_config, min_abbrev=2)
    registry.register("close", _cmd_close, min_abbrev=2)
    registry.register("delete", _cmd_delete, min_abbrev=1)
    registry.register("yank", _cmd_yank, min_abbrev=1)
    registry.register("substitute", _cmd_substitute, min_abbrev=1)
    registry.register("set", _cmd_set, min_abbrev=2)
    registry.register("dos2unix", _cmd_dos2unix, min_abbrev=4)
    registry.register("unix2dos", _cmd_unix2dos, min_abbrev=5)
    registry.register("tabs2spaces", _cmd_tabs2spaces, min_abbrev=4)
    registry.register("normal", _cmd_normal, min_abbrev=2)
    registry.register("echo", _cmd_echo, min_abbrev=2)
    registry.register("nohlsearch", _cmd_nohlsearch, min_abbrev=3)
    registry.register("noh", _cmd_nohlsearch, min_abbrev=3)
    registry.register("map", _cmd_map, min_abbrev=3)
    registry.register("nmap", _cmd_map, min_abbrev=2)
    registry.register("imap", _cmd_map, min_abbrev=2)
    registry.register("vmap", _cmd_map, min_abbrev=2)
    registry.register("noremap", _cmd_map, min_abbrev=3)
    registry.register("nnoremap", _cmd_map, min_abbrev=2)
    registry.register("inoremap", _cmd_map, min_abbrev=2)
    registry.register("unmap", _cmd_map, min_abbrev=2)
    registry.register("!", _cmd_bang, min_abbrev=1)
    registry.register("colorscheme", _cmd_colorscheme, min_abbrev=3)
    registry.register("palette", _cmd_palette, min_abbrev=3)
    registry.register("checkhealth", _cmd_checkhealth, min_abbrev=3)
    registry.register("messages", _cmd_messages, min_abbrev=3)
    registry.register("bdelete", _cmd_bdelete, min_abbrev=2)
    registry.register("split", _cmd_split, min_abbrev=2)
    registry.register("vsplit", _cmd_vsplit, min_abbrev=2)
    registry.register("only", _cmd_only, min_abbrev=2)
    registry.register("tabnew", _cmd_tabnew, min_abbrev=4)
    registry.register("tabnext", _cmd_tabnext, min_abbrev=4)
    registry.register("tabn", _cmd_tabnext, min_abbrev=4)
    registry.register("tabprev", _cmd_tabprev, min_abbrev=4)
    registry.register("tabp", _cmd_tabprev, min_abbrev=4)
    registry.register("tabclose", _cmd_tabclose, min_abbrev=4)
    registry.register("LogOn", _cmd_logon, min_abbrev=3)
    registry.register("LogOff", _cmd_logoff, min_abbrev=4)
    registry.register("LogLevel", _cmd_loglevel, min_abbrev=4)
    registry.register("LogView", _cmd_logview, min_abbrev=4)
    registry.register("LogClear", _cmd_logclear, min_abbrev=4)
    registry.register("RecoverFile", _cmd_recoverfile, min_abbrev=7)
