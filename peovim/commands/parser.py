"""
commands.parser — Ex command parser

Parses the full Vim ex command syntax: [range][cmd][!][args]
Range syntax: %, 1,5, 'a,'b, /pattern/, .+1, $-2, etc.
Returns a ParsedCommand dataclass consumed by CommandRegistry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RangeAddr:
    """A single address in a range: line number, mark, search, or special."""

    kind: str  # "line", "mark", "search", "dot", "dollar", "last_visual"
    value: int | str = 0  # int for "line", str for "mark"/"search"
    offset: int = 0  # +/- offset applied after resolving


@dataclass
class ParsedCommand:  # cm:6c4a9f
    """Result of parsing a full ex command string."""

    range_start: RangeAddr | None = None  # first address (or None)
    range_end: RangeAddr | None = None  # second address (or None)
    all_lines: bool = False  # True for % (whole file)
    cmd: str = ""  # command name
    bang: bool = False  # True if ! suffix
    args: str = ""  # remainder after command name
    raw: str = ""  # original input


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_ADDR_RE = re.compile(
    r"""
    (?P<dot>\.)                     # current line
    | (?P<dollar>\$)                # last line
    | (?P<mark>'[a-zA-Z<>\[\]\{\}]) # mark address
    | (?P<search>/[^/]*/?)          # forward search
    | (?P<bsearch>\?[^?]*\??)       # backward search
    | (?P<line>\d+)                 # absolute line number
    """,
    re.VERBOSE,
)

_OFFSET_RE = re.compile(r"([+-]\d*)")


def _parse_addr(s: str, pos: int) -> tuple[RangeAddr | None, int]:
    """Try to parse a single address starting at pos. Returns (addr, new_pos)."""
    m = _ADDR_RE.match(s, pos)
    if m is None:
        return None, pos

    if m.group("dot"):
        addr = RangeAddr(kind="dot")
    elif m.group("dollar"):
        addr = RangeAddr(kind="dollar")
    elif m.group("mark"):
        addr = RangeAddr(kind="mark", value=m.group("mark")[1])
    elif m.group("search"):
        pat = m.group("search")[1:].rstrip("/")
        addr = RangeAddr(kind="search", value=pat)
    elif m.group("bsearch"):
        pat = m.group("bsearch")[1:].rstrip("?")
        addr = RangeAddr(kind="bsearch", value=pat)
    elif m.group("line"):
        addr = RangeAddr(kind="line", value=int(m.group("line")))
    else:
        return None, pos

    pos = m.end()

    # Parse optional offset: +N or -N
    om = _OFFSET_RE.match(s, pos)
    if om:
        raw = om.group(1)
        if raw in ("+", "-"):
            addr.offset = 1 if raw == "+" else -1
        else:
            addr.offset = int(raw)
        pos = om.end()

    return addr, pos


def parse_ex_command(raw: str) -> ParsedCommand:
    """
    Parse a Vim ex command string (the part after ':').

    Examples:
        "w"             → cmd="w"
        "1,5d"          → range 1-5, cmd="d"
        "%s/foo/bar/g"  → all_lines=True, cmd="s", args="foo/bar/g"
        "'a,'bd"        → mark range, cmd="d"
        "set number"    → cmd="set", args="number"
        "q!"            → cmd="q", bang=True
    """
    result = ParsedCommand(raw=raw)
    s = raw.strip()
    pos = 0

    # --- Range parsing ---
    if pos < len(s) and s[pos] == "%":
        result.all_lines = True
        pos += 1
    else:
        addr1, pos = _parse_addr(s, pos)
        if addr1 is not None:
            result.range_start = addr1
            if pos < len(s) and s[pos] == ",":
                pos += 1
                addr2, pos = _parse_addr(s, pos)
                if addr2 is not None:
                    result.range_end = addr2
                else:
                    # No second address — treat as single-line range where end=start
                    result.range_end = addr1

    # Skip whitespace
    while pos < len(s) and s[pos] == " ":
        pos += 1

    # --- Command name ---
    cmd_start = pos
    while pos < len(s) and s[pos].isalnum():
        pos += 1
    result.cmd = s[cmd_start:pos]

    # Special: '!' as a command character (not alpha, but valid)
    if not result.cmd and pos < len(s) and s[pos] == "!":
        result.cmd = "!"
        pos += 1
        result.args = s[pos:].strip()
        return result

    # --- Bang ---
    if pos < len(s) and s[pos] == "!":
        result.bang = True
        pos += 1

    # --- Args ---
    result.args = s[pos:].strip()

    return result
