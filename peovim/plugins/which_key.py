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
    api.options.define(
        "which_key_sidebar_groups",
        str,
        "",
        doc="Space/comma-separated leader prefixes shown in which-key while a sidebar panel is focused",
    )
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

    active_panel = _active_sidebar_panel(api)
    all_bindings = [b for b in all_bindings if _binding_visible(b, active_panel)]

    leader = _get_leader(api)

    if active_panel is not None:
        allowed = _sidebar_allowed_groups(api)
        if allowed:
            all_bindings = [b for b in all_bindings if _sidebar_group_allowed(b, allowed, leader)]

    # Multiple scope variants of the same key can be visible at once (e.g. a
    # "sidebar" and a "panel" variant). Collapse them to the most-specific one so
    # which-key shows a single named leaf instead of an unnamed "+group".
    all_bindings = _dedupe_scoped(all_bindings)

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


def _active_sidebar_panel(api: Any) -> str | None:
    """Return the focused sidebar panel name, or None when none is focused."""
    try:
        name = api.ui.focused_sidebar_panel_name()
        return name if isinstance(name, str) else None
    except Exception:
        return None


def _binding_visible(b: Any, active_panel: str | None) -> bool:
    """Filter panel-scoped bindings to the currently focused panel."""
    scope = getattr(b, "scope", "")
    if not isinstance(scope, str) or not scope:
        return True
    if scope == "sidebar":
        return active_panel is not None
    if scope == "editor":
        return active_panel is None
    return scope == active_panel


def _sidebar_allowed_groups(api: Any) -> set[str]:
    """Return the configured sidebar-visible leader prefixes (empty = show all)."""
    try:
        raw = api.options.get("which_key_sidebar_groups")
    except Exception:
        return set()
    if not isinstance(raw, str) or not raw:
        return set()
    return {token for token in raw.replace(",", " ").split() if token}


def _sidebar_group_allowed(b: Any, allowed: set[str], leader: str) -> bool:
    """Keep scoped bindings; restrict global bindings to allowed leader groups."""
    scope = getattr(b, "scope", "")
    if isinstance(scope, str) and scope:
        return True  # scoped bindings already filtered by _binding_visible
    return _top_level_leader_key(b.keys, leader) in allowed


def _top_level_leader_key(keys: str, leader: str) -> str:
    """Return the first key token after the leader, or "" for non-leader keys."""
    expanded = keys.replace("<leader>", leader).replace("<Leader>", leader)
    if not expanded.startswith(leader):
        return ""
    return _first_key_token(expanded[len(leader) :])


def _scope_specificity(scope: str) -> int:
    """More-specific scopes win: panel > sidebar > editor > global."""
    if not isinstance(scope, str) or not scope:
        return 0
    if scope == "editor":
        return 1
    if scope == "sidebar":
        return 2
    return 3


def _dedupe_scoped(bindings: list[Any]) -> list[Any]:
    """Keep only the most-specific visible variant for each key sequence."""
    best: dict[str, tuple[int, Any]] = {}
    for b in bindings:
        key = getattr(b, "keys", "")
        spec = _scope_specificity(getattr(b, "scope", ""))
        if key not in best or spec > best[key][0]:
            best[key] = (spec, b)
    return [entry[1] for entry in best.values()]


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
