"""
which_key — Bottom-panel key-binding hints with group support.

Behaviour:
- Panel appears immediately when a multi-key prefix is pending.
- Bindings sharing a next-key are collapsed into a group entry.
- Group names are registered via api.keymap.ngroup("<leader>s", "Search").
- Panel auto-dismisses when the binding completes or is cancelled.

Configuration:
    options.set('which_key_enabled', True)

Group example (in user config or plugin):
    keymap.ngroup("<leader>s", "Search")
    keymap.nmap("<leader>sf", find_fn, desc="Find files")
    keymap.nmap("<leader>sg", grep_fn,  desc="Live grep")
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def setup(api: EditorAPI) -> None:
    api.options.define("which_key_enabled", bool, True, doc="Show which-key popup for pending key prefixes")
    api.events.on("key_prefix_pending", lambda **kw: _on_prefix(api, **kw))
    api.events.on("key_prefix_done", lambda **kw: _hide(api))
    api.commands.register(
        "whichkey",
        lambda cmd, ctx: _show_for_prefix(api, getattr(cmd, "args", "") or ""),
        min_abbrev=8,
    )
    api.keymap.nmap("<leader>?", lambda: _show_for_prefix(api, _get_leader(api)), desc="WhichKey: show leader bindings")


def teardown() -> None:
    pass


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _on_prefix(api: Any, **kwargs: Any) -> None:
    prefix: str = kwargs.get("prefix", "")
    mode: str = kwargs.get("mode", "normal")
    _show_bindings(api, prefix, mode)


def _hide(api: Any) -> None:
    with contextlib.suppress(Exception):
        api.ui.hide_which_key()


def _show_for_prefix(api: Any, prefix: str) -> None:
    _show_bindings(api, prefix, "normal")


# ---------------------------------------------------------------------------
# Core display logic
# ---------------------------------------------------------------------------


def _first_key_token(s: str) -> str:
    """Return the first key token from *s* (handles <special> sequences)."""
    if not s:
        return ""
    if s[0] == "<":
        end = s.find(">")
        if end >= 0:
            return s[: end + 1]
    return s[0]


def _show_bindings(api: Any, prefix: str, mode: str) -> None:
    """Build which-key entries for the current prefix level and show the panel."""
    try:
        if api.options.get("which_key_enabled") is False:
            return
    except Exception:
        pass

    try:
        all_bindings = api.keymap.get_bindings(mode)
    except Exception:
        return

    leader = _get_leader(api)

    def _exp(b: Any) -> str:
        return b.keys.replace("<leader>", leader).replace("<Leader>", leader)

    # Group bindings by the *next* key token after the current prefix
    # next_key → {"leaf": BindingInfo|None, "count": int, "first": BindingInfo|None}
    slots: dict[str, dict] = {}
    for b in all_bindings:
        expanded = _exp(b)
        if not expanded.startswith(prefix) or expanded == prefix:
            continue
        rest = expanded[len(prefix) :]
        nk = _first_key_token(rest)
        if nk not in slots:
            slots[nk] = {"leaf": None, "count": 0, "first": None}
        slots[nk]["count"] += 1
        if slots[nk]["first"] is None:
            slots[nk]["first"] = b
        if rest == nk:  # exact leaf at this level
            slots[nk]["leaf"] = b

    if not slots:
        return

    # Retrieve registered group names
    try:
        get_group = api.keymap.get_group_name
    except Exception:

        def get_group(_prefix: str) -> str:
            return ""

    pairs: list[tuple[str, str]] = []
    for nk, info in sorted(slots.items()):
        if info["count"] == 1:
            # Single binding — always show as a leaf regardless of depth
            b = info["leaf"] or info["first"]
            desc = b.desc if b else ""
            pairs.append((nk, desc))
        else:
            # Multiple bindings → group
            group_prefix_expanded = prefix + nk
            group_prefix_unexpanded = group_prefix_expanded.replace(leader, "<leader>")
            name = get_group(group_prefix_expanded) or get_group(group_prefix_unexpanded) or ""
            label = f"+{name}" if name else "+group"
            pairs.append((nk, f"{label}  ({info['count']} bindings)"))

    # Build a readable title from the prefix (replace leader char with <leader>)
    if prefix:
        display = prefix.replace(leader, "<leader>")
        title = f"Which Key  {display}"
    else:
        title = "Which Key"

    with contextlib.suppress(Exception):
        api.ui.show_which_key(pairs, title=title)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_leader(api: Any) -> str:
    try:
        leader = api.keymap.leader  # property — no ()
        if isinstance(leader, str):
            return leader
    except Exception:
        pass
    return "\\"


# ---------------------------------------------------------------------------
# Public helper (for testing / external use)
# ---------------------------------------------------------------------------


def get_bindings_for_prefix(api: Any, prefix: str, mode: str = "normal") -> list[dict]:
    """Return raw bindings that start with *prefix* (unexpanded keys)."""
    try:
        all_bindings = api.keymap.get_bindings(mode)
    except Exception:
        return []
    return [{"keys": b.keys, "desc": b.desc} for b in all_bindings if b.keys.startswith(prefix) and b.keys != prefix]
