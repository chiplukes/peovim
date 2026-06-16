"""core.diffing — unified diff parser for hunk sign placement.

Parses unified diff output (from `git diff`, `svn diff`, or any tool that
produces the standard unified-diff format) into hunk dicts consumed by
gitsigns and svnsigns for gutter sign placement.
"""

from __future__ import annotations


def parse_hunks(diff_text: str) -> list[dict]:
    """Parse unified diff output into hunk dicts.

    Each hunk dict: {"type": str, "start": int, "end": int}
    where start/end are 0-based line numbers in the new file.

    Hunk types:
      "add"    — lines only in the new file
      "delete" — lines removed; sign placed at the adjacent line
      "change" — mix of additions and deletions in the same hunk
    """
    hunks: list[dict] = []
    new_line = 0
    in_hunk = False
    adds: list[int] = []
    dels: list[int] = []

    def _flush() -> None:
        nonlocal adds, dels
        if not adds and not dels:
            adds = []
            dels = []
            return
        if adds and dels:
            kind = "change"
            start = min(adds + dels)
            end = max(adds + dels)
        elif adds:
            kind = "add"
            start = adds[0]
            end = adds[-1]
        else:
            kind = "delete"
            # Place sign on the line just before the deletion (or line 0)
            start = max(0, dels[0] - 1)
            end = start
        hunks.append({"type": kind, "start": start, "end": end})
        adds = []
        dels = []

    for line in diff_text.splitlines():
        if line.startswith("@@"):
            _flush()
            try:
                new_part = line.split()[2]  # "+new_start[,new_count]"
                new_line = max(0, int(new_part.lstrip("+").split(",")[0]) - 1)
                in_hunk = True
            except (IndexError, ValueError):
                in_hunk = False
            continue

        if not in_hunk:
            continue

        # Skip file header lines that can appear within multi-file diffs
        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("+"):
            adds.append(new_line)
            new_line += 1
        elif line.startswith("-"):
            dels.append(new_line)
        else:
            _flush()
            new_line += 1

    _flush()
    return hunks
