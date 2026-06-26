"""
modal.actions — Action frozen dataclass hierarchy

All actions are immutable dataclasses emitted by the ModalEngine and
consumed by the ActionDispatcher. Actions are the seam between input
and editor state: macro recording, dot-repeat, and the plugin API all
observe or replay them.

See notes/plan_actions.md Part 1 for the complete hierarchy and field
definitions. Categories:
  - Text mutations  (InsertText, DeleteRange, ReplaceRange, ...)
  - Cursor/viewport (MoveCursor, ScrollView, ...)
  - Register ops    (YankRange, PasteRegister, ...)
  - Mode transitions (EnterInsertMode, EnterNormalMode, ...)
  - Undo/redo       (Undo, Redo)
  - Search          (SearchNext, SearchWordUnderCursor)
  - Macros          (StartMacroRecord, StopMacroRecord, PlayMacro)
  - Window/workspace (SplitWindow, CloseWindow, FocusWindow, ...)
  - Buffer lifecycle (SaveBuffer, OpenBuffer, CloseBuffer, QuitEditor)
  - Ex commands     (RunExCommand, RunNormalKeys)
  - Dot repeat      (RepeatLastChange)
  - Plugin          (PlugAction)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Action:  # cm:9f1b3d
    """Base class. All actions are immutable value objects."""

    pass


# ---------------------------------------------------------------------------
# Text mutations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsertText(Action):
    line: int
    col: int  # character offset; Document converts to byte offset
    text: str


@dataclass(frozen=True)
class DeleteRange(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    register: str = '"'
    save_deleted: bool = False
    # Motion metadata for dot-repeat re-evaluation; excluded from equality/hash
    motion_fn: Any = field(default=None, compare=False, hash=False)
    motion_count: int = field(default=1, compare=False, hash=False)
    motion_range_type: str = field(default="char", compare=False, hash=False)
    motion_end_exclusive: bool = field(default=False, compare=False, hash=False)
    motion_end_inclusive: bool = field(default=False, compare=False, hash=False)


@dataclass(frozen=True)
class ReplaceRange(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    new_text: str


@dataclass(frozen=True)
class InsertTab(Action):
    line: int
    col: int  # character offset; dispatcher resolves expandtab/tabstop at dispatch time


@dataclass(frozen=True)
class InsertNewline(Action):
    line: int  # insert newline after this line
    col: int  # character offset where line is split
    indent: str = ""  # auto-computed indent for the new line


@dataclass(frozen=True)
class JoinLines(Action):
    line: int
    count: int = 1


@dataclass(frozen=True)
class ChangeCase(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    mode: Literal["upper", "lower", "toggle"]


@dataclass(frozen=True)
class ChangeCaseBlock(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    mode: Literal["upper", "lower", "toggle"]


@dataclass(frozen=True)
class ReplaceBlock(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    char: str


@dataclass(frozen=True)
class IndentRange(Action):
    start_line: int
    end_line: int
    direction: Literal["in", "out"]
    count: int = 1


@dataclass(frozen=True)
class FormatRange(Action):
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FilterRange(Action):
    start_line: int
    end_line: int
    cmd: str


@dataclass(frozen=True)
class IncrementNumber(Action):
    delta: int  # +1 for <C-a>, -1 for <C-x>


@dataclass(frozen=True)
class RepeatLastExCommand(Action):
    pass


@dataclass(frozen=True)
class CompoundAction(Action):
    """Groups multiple actions into one undo step."""

    actions: tuple[Action, ...]
    description: str = ""


# ---------------------------------------------------------------------------
# Cursor and viewport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoveCursor(Action):
    line: int
    col: int
    add_to_jumplist: bool = False


@dataclass(frozen=True)
class ScrollView(Action):
    lines: int  # positive = down; negative = up


@dataclass(frozen=True)
class ScrollToCursor(Action):
    position: Literal["top", "middle", "bottom"] = "middle"


@dataclass(frozen=True)
class SetVisualAnchor(Action):
    line: int
    col: int


# ---------------------------------------------------------------------------
# Register and yank
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YankRange(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    register: str = '"'
    yank_type: Literal["char", "line", "block"] = "char"


@dataclass(frozen=True)
class YankLine(Action):
    line: int
    count: int = 1
    register: str = '"'


@dataclass(frozen=True)
class YankBlock(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    register: str = '"'


@dataclass(frozen=True)
class DeleteBlock(Action):
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    register: str = '"'
    save_deleted: bool = False


@dataclass(frozen=True)
class BeginBlockInsert(Action):
    start_line: int
    end_line: int
    col: int


@dataclass(frozen=True)
class RepeatBlockInsert(Action):
    row_count: int
    col: int
    text: str


@dataclass(frozen=True)
class PasteRegister(Action):
    register: str = '"'
    before: bool = False
    count: int = 1


# ---------------------------------------------------------------------------
# Mode transitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnterInsertMode(Action):
    position: Literal[
        "cursor",
        "after_cursor",
        "line_start",
        "line_end",
        "new_line_below",
        "new_line_above",
        "col_1",
    ] = "cursor"


@dataclass(frozen=True)
class EnterNormalMode(Action):
    pass


@dataclass(frozen=True)
class EnterVisualMode(Action):
    mode: Literal["char", "line", "block"]


@dataclass(frozen=True)
class EnterCommandMode(Action):
    prompt: Literal[":", "/", "?", "!"] = ":"
    initial: str = ""


@dataclass(frozen=True)
class EnterReplaceMode(Action):
    single: bool = False


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Undo(Action):
    count: int = 1


@dataclass(frozen=True)
class Redo(Action):
    count: int = 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchNext(Action):
    reverse: bool = False
    count: int = 1


@dataclass(frozen=True)
class SearchWordUnderCursor(Action):
    whole_word: bool = True
    reverse: bool = False


@dataclass(frozen=True)
class SetSearchPattern(Action):
    pattern: str
    direction: str = "forward"  # "forward" | "backward"


@dataclass(frozen=True)
class ClearSearchHighlight(Action):
    pass


# ---------------------------------------------------------------------------
# Macros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartMacroRecord(Action):
    register: str


@dataclass(frozen=True)
class StopMacroRecord(Action):
    pass


@dataclass(frozen=True)
class PlayMacro(Action):
    register: str
    count: int = 1


# ---------------------------------------------------------------------------
# Window / workspace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitWindow(Action):
    direction: Literal["h", "v"]
    buffer_path: str | None = None


@dataclass(frozen=True)
class CloseWindow(Action):
    force: bool = False


@dataclass(frozen=True)
class FocusWindow(Action):
    direction: Literal["h", "j", "k", "l"]
    count: int = 1


@dataclass(frozen=True)
class CycleWindow(Action):
    """Cycle focus to the next/prev window across all splits and tabs."""

    direction: Literal["next", "prev"]


@dataclass(frozen=True)
class SmartFocusWindow(Action):
    """Move focus in direction (h/j/k/l); if already at edge, cycle across all splits/tabs."""

    direction: Literal["h", "j", "k", "l"]


@dataclass(frozen=True)
class ResizeWindow(Action):
    direction: Literal["h", "v"]
    delta: int


@dataclass(frozen=True)
class ToggleWindowExpand(Action):
    width_fraction: float = 0.75


@dataclass(frozen=True)
class NewTab(Action):
    pass


@dataclass(frozen=True)
class FocusTab(Action):
    index: int | Literal["next", "prev"]


@dataclass(frozen=True)
class OnlyWindow(Action):
    """Close all windows in the current tab except the active one."""

    pass


@dataclass(frozen=True)
class EqualizeWindows(Action):
    """Resize all windows to equal size (<C-w>=)."""

    pass


@dataclass(frozen=True)
class TabClose(Action):
    force: bool = False


@dataclass(frozen=True)
class NextTab(Action):
    count: int = 1


@dataclass(frozen=True)
class PrevTab(Action):
    count: int = 1


# ---------------------------------------------------------------------------
# Buffer lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SaveBuffer(Action):
    force: bool = False
    path: str | None = None


@dataclass(frozen=True)
class OpenBuffer(Action):
    path: str


@dataclass(frozen=True)
class CloseBuffer(Action):
    force: bool = False


@dataclass(frozen=True)
class QuitEditor(Action):
    force: bool = False


# ---------------------------------------------------------------------------
# Ex commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunExCommand(Action):
    command: str


@dataclass(frozen=True)
class RunNormalKeys(Action):
    keys: str
    remap: bool = False


# ---------------------------------------------------------------------------
# Dot repeat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepeatLastChange(Action):
    count: int = 1


# ---------------------------------------------------------------------------
# Marks and jump list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetMark(Action):
    """m{char} — set a mark at the cursor position."""

    name: str


@dataclass(frozen=True)
class JumpToMark(Action):
    """'{char} or `{char} — jump to a mark.
    line_only=True means jump to first non-blank on mark's line (')
    line_only=False means jump to exact mark position (`)
    """

    name: str
    line_only: bool = False


@dataclass(frozen=True)
class JumpBack(Action):
    """Ctrl-o — jump back in the jump list."""

    count: int = 1


@dataclass(frozen=True)
class JumpForward(Action):
    """Ctrl-i — jump forward in the jump list."""

    count: int = 1


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateFold(Action):
    start_line: int
    end_line: int


@dataclass(frozen=True)
class OpenFold(Action):
    line: int


@dataclass(frozen=True)
class CloseFold(Action):
    line: int


@dataclass(frozen=True)
class ToggleFold(Action):
    line: int


@dataclass(frozen=True)
class OpenAllFolds(Action):
    pass


@dataclass(frozen=True)
class CloseAllFolds(Action):
    pass


@dataclass(frozen=True)
class DeleteFold(Action):
    line: int


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


@dataclass
class PluginContext:  # cm:4e2c6f
    """Calling context snapshotted by the engine when a user binding fires."""

    mode: str  # "normal" | "visual_char" | "visual_line" | "visual_block"
    visual_range: tuple[int, int] | None  # (start_line, end_line) inclusive, or None
    count: int  # numeric count prefix (1 if none)
    register: str  # register prefix ("" if none)
    cursor: tuple[int, int]  # (line, col) at binding time
    is_repeat: bool = False  # True when fired via dot-repeat
    visual_line_count: int = 1  # line count from original visual op (for repeat)


@dataclass
class RunPlugin:
    """Execute a plugin-registered Python callback (internal use by KeymapAPI)."""

    callback_id: int
    ctx: PluginContext | None = None


@dataclass(frozen=True)
class PlugAction(Action):
    name: str
    data: Any = None
