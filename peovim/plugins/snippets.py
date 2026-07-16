"""
snippets — VSCode-format snippet expansion with inline completion menu.

Features
--------
* Loads VSCode .code-snippets JSON files.
* Filetype → snippet-file mapping configured via ``api.options``.
* As you type in insert mode matching snippet prefixes are offered in a
  completion popup.  Press <Tab> or <CR> to expand.
* <C-s> opens the completion popup with **all** snippets for the current
  filetype.
* Basic tab-stop support: $1, $2, ..., $0.

Configuration (in init.py)
--------------------------
.. code-block:: python

    api.options.set("snippets_mappings", {
        "verilog": ["/path/to/verilog.json"],
        "python":  ["/path/to/python.json"],
    })
    # optional: set to False to disable auto-trigger
    api.options.set("snippets_auto_trigger", True)
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state (per-session)
# ---------------------------------------------------------------------------

# filetype → list[dict] (raw snippet dicts from the JSON files)
_snippets: dict[str, list[dict]] = {}

# Active tab-stop state for the currently expanded snippet.
# None when no snippet is active.
_active_snippet: dict | None = None


# ---------------------------------------------------------------------------
# VSCode snippet parser
# ---------------------------------------------------------------------------

_TABSTOP_RE = re.compile(r"\$(\d+)|\$\{(\d+)(?::([^}]*))?\}|\$0")

_CURSORPOS_RE = re.compile(r"\$CURSORPOS")

_VARIABLE_RE = re.compile(
    r"\$"
    r"(TM_FILENAME_BASE|TM_FILENAME|TM_DIRECTORY|TM_FILEPATH|"
    r"CURRENT_YEAR_SHORT|CURRENT_YEAR|"
    r"CURRENT_MONTH_NAME_SHORT|CURRENT_MONTH_NAME|CURRENT_MONTH|"
    r"CURRENT_DAY_NAME_SHORT|CURRENT_DAY_NAME|CURRENT_DATE|"
    r"CURRENT_HOUR|CURRENT_MINUTE|CURRENT_SECOND|"
    r"BLOCK_COMMENT_START|BLOCK_COMMENT_END|LINE_COMMENT)"
    r"(?![A-Z0-9_])"
)


def _resolve_variables(text: str, filepath: str | None) -> str:
    """Replace VSCode snippet variables in *text*."""
    now = datetime.datetime.now()
    month_names = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    day_names = (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    )

    def _repl(m: re.Match) -> str:
        var = m.group(0)[1:]  # strip leading $
        if var == "TM_FILENAME":
            return pathlib.Path(filepath).name if filepath else ""
        if var == "TM_FILENAME_BASE":
            return pathlib.Path(filepath).stem if filepath else ""
        if var == "TM_DIRECTORY":
            return str(pathlib.Path(filepath).parent) if filepath else ""
        if var == "TM_FILEPATH":
            return filepath or ""
        if var == "CURRENT_YEAR":
            return f"{now.year:04d}"
        if var == "CURRENT_YEAR_SHORT":
            return f"{now.year % 100:02d}"
        if var == "CURRENT_MONTH":
            return f"{now.month:02d}"
        if var == "CURRENT_MONTH_NAME":
            return month_names[now.month - 1]
        if var == "CURRENT_MONTH_NAME_SHORT":
            return month_names[now.month - 1][:3]
        if var == "CURRENT_DATE":
            return f"{now.day:02d}"
        if var == "CURRENT_DAY_NAME":
            return day_names[now.weekday()]
        if var == "CURRENT_DAY_NAME_SHORT":
            return day_names[now.weekday()][:3]
        if var == "CURRENT_HOUR":
            return f"{now.hour:02d}"
        if var == "CURRENT_MINUTE":
            return f"{now.minute:02d}"
        if var == "CURRENT_SECOND":
            return f"{now.second:02d}"
        if var == "BLOCK_COMMENT_START":
            return "/*"
        if var == "BLOCK_COMMENT_END":
            return "*/"
        if var == "LINE_COMMENT":
            return "//"
        return m.group(0)

    return _VARIABLE_RE.sub(_repl, text)


def _parse_tabstops(body_lines: list[str]) -> tuple[str, list[tuple[int, int, int]], tuple[int, int] | None]:
    """Parse $1, $2, ..., $0, $CURSORPOS from snippet body lines.

    Returns (plain_text, stops, cursor_pos) where:
    - plain_text has all markers replaced with their defaults ("" for $0)
    - stops is list of (tabstop_index, line, col) sorted by tabstop index
    - cursor_pos is (line, col) of the $0/$CURSORPOS marker, or None
    """
    plain_lines: list[str] = []
    stops: list[tuple[int, int, int]] = []
    cursor_pos: tuple[int, int] | None = None

    for li, line in enumerate(body_lines):
        m = _CURSORPOS_RE.search(line)
        if m:
            line = line[: m.start()] + line[m.end():]
            cursor_pos = (li, m.start())

        result = []
        col = 0
        for m in _TABSTOP_RE.finditer(line):
            result.append(line[col : m.start()])
            idx = int(m.group(1) or m.group(2) or 0)
            default_text = m.group(3) or ""
            if idx == 0:
                result.append(default_text)
                cursor_pos = (li, len("".join(result)) - len(default_text))
            else:
                stops.append((idx, li, len("".join(result))))
                result.append(default_text)
            col = m.end()
        result.append(line[col:])
        plain_lines.append("".join(result))

    stops.sort(key=lambda s: (s[0], s[1], s[2]))
    return "\n".join(plain_lines), stops, cursor_pos


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_LOAD_COMMENT_RE = re.compile(r'//[^\n]*|/\*.*?\*/', re.DOTALL)
_LOAD_TRAILING_COMMA_RE = re.compile(r',\s*([}\]])')


def _load_snippet_file(filepath: str) -> dict[str, dict]:
    """Load a VSCode-format .code-snippets JSON file (supports // comments)."""
    raw_text = ""
    try:
        with open(filepath, encoding="utf-8") as f:
            raw_text = f.read()
        raw_text = _LOAD_COMMENT_RE.sub("", raw_text)
        raw_text = _LOAD_TRAILING_COMMA_RE.sub(r"\1", raw_text)
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            log.warning("snippets: %s is not a dict, skipping", filepath)
            return {}
        return raw
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("snippets: cannot load %s: %s", filepath, exc)
        if raw_text:
            log.warning("snippets:   stripped content starts: %r", raw_text[:300])
        return {}


def _reload(api: EditorAPI) -> None:
    """Reload all snippet files from the mappings option."""
    import platformdirs

    global _snippets
    _snippets.clear()
    mappings = api.options.get("snippets_mappings") or {}
    if not isinstance(mappings, dict):
        log.warning("snippets: snippets_mappings is not a dict: %r", mappings)
        return

    config_dir = pathlib.Path(platformdirs.user_config_dir("peovim"))
    log.debug("snippets: config_dir=%s, mappings=%s", config_dir, list(mappings.keys()))

    for filetype, paths in mappings.items():
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            continue
        all_snippets: list[dict] = []
        for p in paths:
            filepath = pathlib.Path(p)
            if not filepath.is_absolute():
                filepath = config_dir / filepath
            log.debug("snippets: loading %s → %s", filetype, filepath)
            raw = _load_snippet_file(str(filepath))
            if raw:
                for _name, snip in raw.items():
                    if isinstance(snip, dict) and "prefix" in snip and "body" in snip:
                        all_snippets.append(snip)
        if all_snippets:
            _snippets[filetype] = all_snippets
            log.info("snippets: %s → %d snippets loaded", filetype, len(all_snippets))
        else:
            log.warning("snippets: %s → no snippets loaded", filetype)
    log.debug("snippets: loaded %d filetypes", len(_snippets))


# ---------------------------------------------------------------------------
# Completion items
# ---------------------------------------------------------------------------

def _make_completion_items(
    snip_list: list[dict], filter_prefix: str = "", *, filepath: str | None = None, indent: str = ""
) -> list[dict]:
    """Build CompletionPopup items from snippet dicts, optionally filtered.

    *indent* is prepended to each line after the first of the snippet body so
    multi-line snippets match the surrounding indentation.
    """
    items: list[dict] = []
    for snip in snip_list:
        prefix = snip.get("prefix", "")
        if isinstance(prefix, list):
            prefix = prefix[0] if prefix else ""
        if not prefix:
            continue
        if filter_prefix and not prefix.lower().startswith(filter_prefix.lower()):
            continue
        description = snip.get("description", prefix)
        body = snip.get("body", [])
        if isinstance(body, str):
            body = [body]
        body_lines = list(body)
        body_text = "\n".join(body_lines)
        body_text = _resolve_variables(body_text, filepath)
        lines = body_text.split("\n")
        if indent and len(lines) > 1:
            lines = [lines[0]] + [indent + ln for ln in lines[1:]]
        plain_body, _stops, cursor_pos = _parse_tabstops(lines)

        item: dict = {
            "label": prefix,
            "kind": 15,  # "snp" (Snippet)
            "detail": description,
            "insertText": plain_body,
            "filterText": prefix,
        }
        if cursor_pos is not None:
            item["cursorLine"] = cursor_pos[0]
            item["cursorCol"] = cursor_pos[1]
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

def _current_word_prefix(api: EditorAPI) -> tuple[int, str]:
    """Return (prefix_start_col, prefix_text) for the word at cursor."""
    win = api._workspace.active_window
    doc = win.document
    line = win.cursor.line
    col = win.cursor.col
    line_text = doc.get_line(line) or ""
    prefix_end = max(0, min(col, len(line_text)))
    prefix_start = prefix_end
    while prefix_start > 0 and line_text[prefix_start - 1 : prefix_start].isalnum():
        prefix_start -= 1
    return prefix_start, line_text[prefix_start:prefix_end]


def _get_snippets(api: EditorAPI, filetype: str) -> list[dict]:
    """Return snippets for *filetype*, lazily reloading if needed."""
    global _snippets
    if filetype and filetype not in _snippets:
        _reload(api)
    return _snippets.get(filetype, [])


def _is_insert_mode(api: EditorAPI) -> bool:
    mode = getattr(api, "active_mode", None)
    mode_name = getattr(mode, "value", mode)
    return mode_name in {"insert", "replace"}


def _current_filepath(api: EditorAPI) -> str | None:
    buf = api.active_buffer()
    p = getattr(buf, "path", None)
    return str(p) if p else None


def _current_indent(api: EditorAPI) -> str:
    """Return the leading whitespace of the current line."""
    win = api._workspace.active_window
    doc = win.document
    line_text = doc.get_line(win.cursor.line) or ""
    return line_text[: len(line_text) - len(line_text.lstrip())]


def _show_completions(api: EditorAPI, items: list[dict], prefix: str) -> None:
    """Open the CompletionPopup with snippet items."""
    if not items:
        return
    event_loop = getattr(api, "_event_loop", None)
    popup = getattr(event_loop, "_completion_popup", None)
    if popup is None or event_loop is None:
        return
    prefix_start, _ = _current_word_prefix(api)
    popup.open(
        items,
        api._workspace.active_window.cursor.line,
        prefix_start,
        filter_text=prefix,
        match_mode="prefix",
        replace_filter_on_accept=True,
    )
    event_loop._invalidate("full")


# ---------------------------------------------------------------------------
# Public API (keymap callbacks)
# ---------------------------------------------------------------------------

def trigger(api: EditorAPI) -> None:
    """Open the completion popup with all snippets for the current filetype."""
    buf = api.active_buffer()
    ft = getattr(buf, "filetype", "") or ""
    log.debug("snippets: trigger filetype=%r", ft)
    snip_list = _get_snippets(api, ft)
    if not snip_list:
        log.debug("snippets: no snippets for filetype=%r", ft)
        return
    items = _make_completion_items(snip_list, filepath=_current_filepath(api), indent=_current_indent(api))
    if not items:
        return
    prefix_start, prefix = _current_word_prefix(api)
    log.debug("snippets: showing %d items, prefix=%r", len(items), prefix)
    _show_completions(api, items, prefix)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _on_text_changed(api: EditorAPI, **kw: object) -> None:
    """Auto-trigger: check if current word prefix matches any snippet."""
    if not api.options.get("snippets_auto_trigger", True):
        return
    if not _is_insert_mode(api):
        return

    buf = api.active_buffer()
    ft = getattr(buf, "filetype", "") or ""
    snip_list = _get_snippets(api, ft)
    if not snip_list:
        return

    prefix_start, prefix = _current_word_prefix(api)
    if not prefix:
        return

    items = _make_completion_items(snip_list, filter_prefix=prefix, filepath=_current_filepath(api))
    if len(items) == 0:
        # Close an existing popup if the prefix no longer matches
        event_loop = getattr(api, "_event_loop", None)
        popup = getattr(event_loop, "_completion_popup", None)
        if popup is not None and popup.is_open:
            popup.close()
            if event_loop is not None:
                event_loop._invalidate("full")
        return

    if len(items) == 1 and items[0]["label"] == prefix:
        # Exact match — update popup filter so <Tab> still works
        return

    _show_completions(api, items, prefix)


def _on_insert_left(api: EditorAPI, **kw: object) -> None:
    """Close the completion popup when leaving insert mode."""
    event_loop = getattr(api, "_event_loop", None)
    popup = getattr(event_loop, "_completion_popup", None)
    if popup is not None and popup.is_open:
        popup.close()
        if event_loop is not None:
            event_loop._invalidate("full")


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------

def setup(api: EditorAPI) -> None:
    _reload(api)

    api.options.define("snippets_mappings", dict, {}, doc="Filetype → list of .json snippet file paths.")
    api.options.define("snippets_auto_trigger", bool, True, doc="Auto-show snippet completions while typing in insert mode.")

    api.events.on("buffer_text_changed", lambda **kw: _on_text_changed(api, **kw))
    api.events.on("insert_left", lambda **kw: _on_insert_left(api, **kw))
    api.events.on("editor_ready", lambda **kw: _reload(api))

    api.keymap.imap("<C-s>", lambda: trigger(api), desc="Snippets: show all for filetype")

    log.info("snippets: plugin ready")


def teardown() -> None:
    global _snippets, _active_snippet
    _snippets.clear()
    _active_snippet = None
