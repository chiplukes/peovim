from __future__ import annotations

GIT_MODIFIED_COLOR = (220, 200, 90)
GIT_UNTRACKED_COLOR = (80, 200, 80)
GIT_DELETED_COLOR = (220, 110, 110)


def color_for_status_entry(entry: object, *, surface: str) -> tuple[int, int, int] | None:
    if getattr(entry, "untracked", False) and not (
        getattr(entry, "modified", False) or getattr(entry, "staged", False) or getattr(entry, "conflicted", False)
    ):
        return GIT_UNTRACKED_COLOR
    if getattr(entry, "deleted", False):
        return GIT_DELETED_COLOR if surface == "panel" else None
    if getattr(entry, "modified", False) or getattr(entry, "staged", False) or getattr(entry, "conflicted", False):
        return GIT_MODIFIED_COLOR
    return None


def marker_for_status_entry(entry: object) -> str:
    if getattr(entry, "mixed", False):
        return "*"
    if getattr(entry, "untracked", False) and not (
        getattr(entry, "modified", False) or getattr(entry, "staged", False) or getattr(entry, "conflicted", False)
    ):
        return "!"
    if getattr(entry, "index_status", "") == "A":
        return "+"
    if getattr(entry, "code", ""):
        return "~"
    return ""
