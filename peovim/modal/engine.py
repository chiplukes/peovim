"""
modal.engine — ModalEngine: KeyEvent stream → Action list

Parses keyboard input into Actions using a structured ParseState accumulator
and a per-mode key binding trie. Implements Vim's command grammar:
  [count_a] ['"' register] [operator] [count_b] (motion | text_object)

See notes/plan_actions.md Part 2 for the full FSM specification including:
  - Mode enum (NORMAL, INSERT, REPLACE, VISUAL_*, COMMAND, OPERATOR_PENDING)
  - ParseState fields and count multiplication rules
  - TrieNode structure
  - Normal mode parsing algorithm
  - Insert / visual mode key tables
  - Multi-key sequence handling (g, z, f, t, r, m, @, ", ...)
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peovim.core.document import Document
    from peovim.core.window import Window

from peovim.modal.actions import (
    Action,
    BeginBlockInsert,
    ChangeCase,
    ChangeCaseBlock,
    CloseAllFolds,
    CloseFold,
    CloseWindow,
    CompoundAction,
    CreateFold,
    DeleteBlock,
    DeleteFold,
    DeleteRange,
    EnterCommandMode,
    EnterInsertMode,
    EnterNormalMode,
    EnterReplaceMode,
    EnterVisualMode,
    EqualizeWindows,
    FocusWindow,
    FormatRange,
    IncrementNumber,
    IndentRange,
    InsertNewline,
    InsertTab,
    InsertText,
    JumpBack,
    JumpForward,
    JumpToMark,
    MoveCursor,
    NextTab,
    OnlyWindow,
    OpenAllFolds,
    OpenBuffer,
    OpenFold,
    PasteRegister,
    PlayMacro,
    PrevTab,
    QuitEditor,
    Redo,
    RepeatLastChange,
    RepeatLastExCommand,
    ReplaceBlock,
    ReplaceRange,
    ResizeWindow,
    SaveBuffer,
    ScrollToCursor,
    ScrollView,
    SearchNext,
    SearchWordUnderCursor,
    SetMark,
    SplitWindow,
    StartMacroRecord,
    StopMacroRecord,
    ToggleFold,
    Undo,
    YankBlock,
    YankLine,
    YankRange,
)

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


class Mode(enum.Enum):  # cm:b2d4f1
    NORMAL = "normal"
    INSERT = "insert"
    REPLACE = "replace"
    VISUAL_CHAR = "visual_char"
    VISUAL_LINE = "visual_line"
    VISUAL_BLOCK = "visual_block"
    COMMAND = "command"
    OPERATOR_PENDING = "operator_pending"
    SELECT = "select"


# Keys that begin multi-key sequences in normal mode
_MULTI_KEY_STARTERS = frozenset(
    {
        "g",
        "z",
        "Z",
        "q",
        "@",
        "f",
        "F",
        "t",
        "T",
        "m",
        "'",
        "`",
        "r",
        "[",
        "]",
        "<C-w>",
    }
)

# Valid register names after "
_REGISTER_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-*/_=:.%#-"')


# ---------------------------------------------------------------------------
# ParseState
# ---------------------------------------------------------------------------


@dataclass
class ParseState:
    """Accumulates components of a Vim command as keys arrive."""

    count_a: int = 0
    register: str = '"'
    operator: str | None = None
    count_b: int = 0
    # key_buffer: accumulates keys for multi-char sequences like gg, zz, f<char>
    key_buffer: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.count_a = 0
        self.register = '"'
        self.operator = None
        self.count_b = 0
        self.key_buffer.clear()

    def effective_count_a(self) -> int:
        return self.count_a if self.count_a > 0 else 1

    def effective_count_b(self) -> int:
        return self.count_b if self.count_b > 0 else 1

    def effective_count(self) -> int:
        return self.effective_count_a() * self.effective_count_b()


# ---------------------------------------------------------------------------
# TrieNode
# ---------------------------------------------------------------------------

ActionFn = Callable[[ParseState], list[Action]]
# Raw motion fn: (doc, line, col, count) -> (new_line, new_col)
MotionFn = Callable


@dataclass
class TrieNode:
    action_fn: ActionFn | None = None
    children: dict[str, TrieNode] = field(default_factory=dict)
    is_operator: bool = False
    is_motion: bool = False
    motion_fn: MotionFn | None = None  # set on motion nodes for operator resolution
    range_type: str = "char"  # "char" or "line" — affects operator range
    motion_end_exclusive: bool = False
    motion_end_inclusive: bool = False


# ---------------------------------------------------------------------------
# Text object resolver (used by visual mode)
# ---------------------------------------------------------------------------


def _resolve_text_object(doc: object, line: int, col: int, obj_key: str, mode: str) -> tuple | None:
    """Return (sl, sc, el, ec) for a text object, or None if not recognized."""
    from peovim.modal.text_objects import (
        angle_inner,
        angle_outer,
        backtick_inner,
        backtick_outer,
        brace_inner,
        brace_outer,
        bracket_inner,
        bracket_outer,
        dquote_inner,
        dquote_outer,
        paragraph_inner,
        paragraph_outer,
        paren_inner,
        paren_outer,
        squote_inner,
        squote_outer,
        word_inner,
        word_outer,
    )

    inner = mode == "inner"
    _map: dict = {
        "w": word_inner if inner else word_outer,
        "(": paren_inner if inner else paren_outer,
        ")": paren_inner if inner else paren_outer,
        "b": paren_inner if inner else paren_outer,
        "{": brace_inner if inner else brace_outer,
        "}": brace_inner if inner else brace_outer,
        "B": brace_inner if inner else brace_outer,
        "[": bracket_inner if inner else bracket_outer,
        "]": bracket_inner if inner else bracket_outer,
        "<": angle_inner if inner else angle_outer,
        ">": angle_inner if inner else angle_outer,
        '"': dquote_inner if inner else dquote_outer,
        "'": squote_inner if inner else squote_outer,
        "`": backtick_inner if inner else backtick_outer,
        "p": paragraph_inner if inner else paragraph_outer,
    }
    fn = _map.get(obj_key)
    if fn is None:
        return None
    try:
        return fn(doc, line, col)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ModalEngine
# ---------------------------------------------------------------------------


class ModalEngine:  # cm:5c8e7a
    """
    Parses a stream of key strings into lists of Actions.
    Call feed_key(key: str) for each key. Returns list[Action].
    The engine auto-updates its mode when mode-transition actions are produced.
    """

    def __init__(self) -> None:
        self.mode: Mode = Mode.NORMAL
        self._state: ParseState = ParseState()
        self._builtin_tries: dict[Mode, TrieNode] = {m: TrieNode() for m in Mode}
        self._user_tries: dict[Mode, TrieNode] = {m: TrieNode() for m in Mode}
        self._recording: bool = False
        self._macro_register: str = ""
        self._macro_buffer: list[str] = []
        self._last_macro: str = ""
        # Register-pending flag: set True after '"' in normal mode
        self._register_pending: bool = False
        # Visual anchor: the position where visual selection started
        self._visual_anchor: tuple[int, int] = (0, 0)
        self._last_visual_selection: tuple[Mode, tuple[int, int], tuple[int, int]] | None = None
        # Cursor/buffer state — updated by the dispatcher via set_cursor/set_line_count
        # or pulled from _provider at the start of each feed_key call.
        self._cursor: tuple[int, int] = (0, 0)
        self._line_count: int = 1
        self._scroll_line: int = 0
        # Document reference for motion resolution (optional; motions degrade gracefully)
        self._document: Document | None = None
        # Live provider: if set, overrides all shadow-state pushes at feed_key time.
        self._provider: Callable[[], Window] | None = None
        self._filter_range: tuple[int, int] | None = None  # set by !{motion}
        self._last_ex_command: str = ""  # for @: repeat
        # Set during block insert so <BS>/<Del> can't eat past the insert column
        self._block_insert_col: int | None = None

        self._register_builtins()

    # ------------------------------------------------------------------
    # External state setters (called by dispatcher)
    # ------------------------------------------------------------------

    def set_cursor(self, line: int, col: int) -> None:
        self._cursor = (line, col)

    def set_line_count(self, count: int) -> None:
        self._line_count = count

    def set_document(self, doc: Document) -> None:
        """Called by dispatcher to give the engine access to document content."""
        self._document = doc

    def set_scroll(self, scroll_line: int) -> None:
        self._scroll_line = scroll_line

    def set_context_provider(self, provider: Callable[[], Window]) -> None:
        """Wire a live provider so feed_key always reads fresh cursor/document state."""
        self._provider = provider

    def _sync_from_provider(self) -> None:
        """Pull cursor, line_count, scroll, and document from the live provider."""
        if self._provider is None:
            return
        win = self._provider()
        self._cursor = (win.cursor.line, win.cursor.col)
        self._line_count = win.document.line_count()
        self._scroll_line = win.scroll_line
        self._document = win.document

    def consume_filter_range(self) -> tuple[int, int] | None:
        """Return and clear the pending filter range (set by ! operator)."""
        r = self._filter_range
        self._filter_range = None
        return r

    def set_last_ex_command(self, cmd: str) -> None:
        self._last_ex_command = cmd

    def set_block_insert_col(self, col: int | None) -> None:
        """Called by dispatcher when entering/leaving block insert mode."""
        self._block_insert_col = col

    def set_mode(self, mode: Mode) -> None:
        """Called by dispatcher after processing mode transition actions."""
        self.mode = mode
        self._state.reset()
        self._register_pending = False

    def set_visual_anchor(self, line: int, col: int) -> None:
        """Set the visual selection anchor (called by dispatcher on EnterVisualMode)."""
        self._visual_anchor = (line, col)

    def visual_char_bounds(self, cursor: tuple[int, int] | None = None) -> tuple[int, int, int, int] | None:
        """Return normalized exclusive-end bounds for visual character mode."""
        if self.mode != Mode.VISUAL_CHAR:
            return None

        anchor_line, anchor_col = self._visual_anchor
        cursor_line, cursor_col = cursor or self._cursor
        start = min((anchor_line, anchor_col), (cursor_line, cursor_col))
        end = max((anchor_line, anchor_col), (cursor_line, cursor_col))
        end_col = end[1] + 1
        if self._document is not None and end[0] < self._document.line_count():
            end_col = min(end_col, len(self._document.get_line(end[0])))
        return (start[0], start[1], end[0], end_col)

    def visual_selection_regions(
        self,
        cursor: tuple[int, int] | None = None,
    ) -> list[tuple[int, int, int, int]]:
        """Return the active visual selection as HighlightRegion-style spans."""
        if self.mode not in (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK):
            return []

        anchor_line, anchor_col = self._visual_anchor
        cursor_line, cursor_col = cursor or self._cursor

        if self.mode == Mode.VISUAL_LINE:
            start_line = min(anchor_line, cursor_line)
            end_line = max(anchor_line, cursor_line)
            return [(start_line, 0, end_line, 0x7FFFFFFF)]

        if self.mode == Mode.VISUAL_CHAR:
            bounds = self.visual_char_bounds(cursor)
            if bounds is None:
                return []
            return [bounds]

        min_col = min(anchor_col, cursor_col)
        max_col = max(anchor_col, cursor_col)
        start_line = min(anchor_line, cursor_line)
        end_line = max(anchor_line, cursor_line)
        return [(line, min_col, line, max_col + 1) for line in range(start_line, end_line + 1)]

    def visual_block_bounds(self, cursor: tuple[int, int] | None = None) -> tuple[int, int, int, int] | None:
        """Return normalized block bounds for visual block mode."""
        if self.mode != Mode.VISUAL_BLOCK:
            return None
        anchor_line, anchor_col = self._visual_anchor
        cursor_line, cursor_col = cursor or self._cursor
        return (
            min(anchor_line, cursor_line),
            min(anchor_col, cursor_col),
            max(anchor_line, cursor_line),
            max(anchor_col, cursor_col) + 1,
        )

    def _remember_visual_selection(self, cursor: tuple[int, int] | None = None) -> None:
        if self.mode not in (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK):
            return
        self._last_visual_selection = (self.mode, self._visual_anchor, cursor or self._cursor)

    def _visual_scroll_actions(self, delta: int, line: int, col: int) -> list[Action]:
        target_line = max(0, min(self._line_count - 1, line + delta))
        return [ScrollView(delta), MoveCursor(target_line, col)]

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def feed_key(self, key: str, remap: bool = True) -> list[Action]:
        """Process one key string. Returns list[Action] (empty while accumulating)."""
        self._sync_from_provider()
        if self.mode == Mode.INSERT:
            actions = self._feed_insert(key, remap=remap)
        elif self.mode in (Mode.VISUAL_CHAR, Mode.VISUAL_LINE, Mode.VISUAL_BLOCK):
            actions = self._feed_visual(key)
        elif self.mode == Mode.NORMAL:
            # While recording: q alone stops recording
            if self._recording and key == "q" and not self._state.key_buffer:
                self._recording = False
                actions = [StopMacroRecord()]
            else:
                if self._recording:
                    self._macro_buffer.append(key)
                actions = self._feed_normal(key)
        elif self.mode == Mode.REPLACE:
            actions = self._feed_replace(key)
        else:
            return []

        # Auto-update mode based on actions produced (so engine is self-contained for tests)
        self._auto_update_mode(actions)
        return actions

    def _auto_update_mode(self, actions: list[Action]) -> None:
        for a in actions:
            if isinstance(a, EnterInsertMode):
                self.mode = Mode.INSERT
                self._state.reset()
                self._register_pending = False
            elif isinstance(a, EnterNormalMode):
                self.mode = Mode.NORMAL
                self._state.reset()
                self._register_pending = False
            elif isinstance(a, EnterVisualMode):
                if a.mode == "char":
                    self.mode = Mode.VISUAL_CHAR
                elif a.mode == "line":
                    self.mode = Mode.VISUAL_LINE
                else:
                    self.mode = Mode.VISUAL_BLOCK
                self._visual_anchor = self._cursor
                self._state.reset()
                self._register_pending = False
            elif isinstance(a, EnterCommandMode):
                self.mode = Mode.COMMAND
                self._state.reset()
                self._register_pending = False
            elif isinstance(a, EnterReplaceMode):
                self.mode = Mode.REPLACE
                self._state.reset()

    # ------------------------------------------------------------------
    # Normal mode FSM
    # ------------------------------------------------------------------

    def _feed_normal(self, key: str) -> list[Action]:
        state = self._state

        # --- Register-pending: waiting for register name after '"' ---
        if self._register_pending:
            self._register_pending = False
            if key in _REGISTER_CHARS:
                state.register = key
                return []
            # Invalid register key — reset
            state.reset()
            return []

        # --- Multi-key sequence in progress (key_buffer non-empty) ---
        if state.key_buffer:
            state.key_buffer.append(key)
            seq = list(state.key_buffer)
            result = self._resolve_multi_key(seq, state)
            if result is not None:
                state.reset()
                return result
            # Check if still a valid prefix (needs more keys)
            if self._is_normal_prefix(seq):
                return []
            # No match — clear and try fresh (drop partial sequence)
            state.key_buffer.clear()
            # Fall through: try the current key fresh

        # --- Count_a (only before operator is set) ---
        if state.operator is None:
            if key.isdigit() and key != "0":
                state.count_a = state.count_a * 10 + int(key)
                return []
            if key == "0" and state.count_a > 0:
                state.count_a = state.count_a * 10
                return []

        # --- Register prefix '"' ---
        if key == '"' and state.operator is None:
            self._register_pending = True
            return []

        # --- Count_b (after operator is set) ---
        if state.operator is not None:
            if key.isdigit() and key != "0":
                state.count_b = state.count_b * 10 + int(key)
                return []
            if key == "0" and state.count_b > 0:
                state.count_b = state.count_b * 10
                return []

        # --- Multi-key prefix starters ---
        if key in _MULTI_KEY_STARTERS and state.operator is None:
            state.key_buffer = [key]
            return []

        # --- Operator-pending: doubled operator = linewise ---
        if state.operator is not None:
            op = state.operator
            if key == op:
                result = self._resolve_linewise_operator(state)
                state.reset()
                return result

            # Text object prefix
            if key in ("i", "a"):
                state.key_buffer = [key]
                return []

            # Multi-char motion prefix in operator-pending
            if key in _MULTI_KEY_STARTERS:
                state.key_buffer = [key]
                return []

            # Check user trie for bindings starting with op+key (e.g. ds(, cs[, ysiw()
            u_node = self._user_tries[Mode.NORMAL]
            for k in (op, key):
                u_node = u_node.children.get(k)
                if u_node is None:
                    break
            if u_node is not None:
                if u_node.action_fn is not None:
                    # Complete 2-char user binding
                    actions = u_node.action_fn(state)
                    state.reset()
                    return actions
                elif u_node.children:
                    # More keys needed (e.g. ds + char) — switch to multi-key mode
                    state.operator = None
                    state.key_buffer = [op, key]
                    return []

        # --- Trie lookup ---
        node = self._lookup_key(key, Mode.NORMAL)

        if node is None:
            state.reset()
            return []

        if state.operator is None:
            # Check for operator node
            if node.is_operator and node.action_fn is None:
                state.operator = key
                return []
            # Prefix node — start multi-key
            if node.action_fn is None and node.children:
                state.key_buffer = [key]
                return []
            if node.action_fn is not None:
                actions = node.action_fn(state)
                state.reset()
                return actions
        else:
            # Operator-pending: look for motion.
            # If the resolved node is a user binding that isn't a motion, fall back
            # to the builtin trie so that keys like 'e' still work as motions even
            # when a plugin has mapped 'e' to a non-motion action (e.g. dashboard).
            if not node.is_motion:
                builtin = self._builtin_tries[Mode.NORMAL].children.get(key)
                if builtin is not None and builtin.is_motion:
                    node = builtin
            if node.is_motion:
                if node.motion_fn is not None and self._document is not None:
                    # Use motion_fn for accurate operator resolution
                    line, col = self._cursor
                    count = state.effective_count()
                    new_line, new_col = node.motion_fn(self._document, line, col, count)
                    actions = self._resolve_operator_motion(
                        state,
                        (line, col),
                        (new_line, new_col),
                        node.range_type,
                        motion_end_exclusive=node.motion_end_exclusive,
                        motion_end_inclusive=node.motion_end_inclusive,
                        motion_fn=node.motion_fn,
                        motion_count=count,
                    )
                    state.reset()
                    return actions
                elif node.action_fn is not None:
                    # Fallback: use action_fn (won't properly apply operator)
                    actions = node.action_fn(state)
                    state.reset()
                    return actions

        state.reset()
        return []

    def _resolve_operator_motion(
        self,
        state: ParseState,
        cursor: tuple[int, int],
        target: tuple[int, int],
        range_type: str,
        *,
        motion_end_exclusive: bool = False,
        motion_end_inclusive: bool = False,
        motion_fn: MotionFn | None = None,
        motion_count: int = 1,
    ) -> list[Action]:
        """Produce Action(s) for a completed operator+motion pair."""
        LINE_END_VAL = 0x7FFFFFFF
        op = state.operator
        reg = state.register

        if range_type == "line":
            start = (min(cursor[0], target[0]), 0)
            end = (max(cursor[0], target[0]), LINE_END_VAL)
        else:
            start = min(cursor, target)
            end = max(cursor, target)
            if motion_end_exclusive and cursor <= target:
                end = target
                if self._document is not None and target[0] < self._document.line_count():
                    line_text = self._document.get_line(target[0])
                    if target[1] >= max(0, len(line_text) - 1):
                        end = (target[0], len(line_text))
            elif motion_end_inclusive and self._document is not None:
                # Inclusive: include the character at `end` (the farther position).
                # Works for both forward (end == target) and backward (end == cursor).
                line_text = self._document.get_line(end[0])
                end = (end[0], min(end[1] + 1, len(line_text)))

        if op == "d":
            return [DeleteRange(
                start[0], start[1], end[0], end[1],
                register=reg, save_deleted=True,
                motion_fn=motion_fn,
                motion_count=motion_count,
                motion_range_type=range_type,
                motion_end_exclusive=motion_end_exclusive,
                motion_end_inclusive=motion_end_inclusive,
            )]
        if op == "y":
            return [YankRange(start[0], start[1], end[0], end[1], register=reg, yank_type=range_type)]
        if op == "c":
            return [
                CompoundAction(
                    (
                        DeleteRange(start[0], start[1], end[0], end[1], register=reg, save_deleted=True),
                        EnterInsertMode("cursor"),
                    ),
                    "change",
                )
            ]
        if op == ">":
            return [IndentRange(start[0], end[0], "in", state.effective_count())]
        if op == "<":
            return [IndentRange(start[0], end[0], "out", state.effective_count())]
        if op == "=":
            return [FormatRange(start[0], end[0])]
        if op == "g~":
            return [ChangeCase(start[0], start[1], end[0], end[1], "toggle")]
        if op == "gu":
            return [ChangeCase(start[0], start[1], end[0], end[1], "lower")]
        if op == "gU":
            return [ChangeCase(start[0], start[1], end[0], end[1], "upper")]
        if op == "!":
            self._filter_range = (min(cursor[0], target[0]), max(cursor[0], target[0]))
            return [EnterCommandMode("!")]
        return []

    def _resolve_linewise_operator(self, state: ParseState) -> list[Action]:
        op = state.operator
        count = state.effective_count()
        line, col = self._cursor
        end_line = min(line + count - 1, self._line_count - 1)

        if op == "d":
            return [DeleteRange(line, 0, end_line, 0x7FFFFFFF, register=state.register, save_deleted=True)]
        if op == "y":
            return [YankLine(line, count, state.register)]
        if op == "c":
            return [
                CompoundAction(
                    (
                        DeleteRange(line, 0, end_line, 0x7FFFFFFF, register=state.register, save_deleted=True),
                        EnterInsertMode("cursor"),
                    ),
                    "change-line",
                )
            ]
        if op == ">":
            return [IndentRange(line, end_line, "in", count)]
        if op == "<":
            return [IndentRange(line, end_line, "out", count)]
        if op == "=":
            return [FormatRange(line, end_line)]
        if op == "!":
            self._filter_range = (line, end_line)
            return [EnterCommandMode("!")]
        return []

    def _resolve_multi_key(self, seq: list[str], state: ParseState) -> list[Action] | None:
        """Try to complete a multi-key sequence. Returns actions or None (still waiting)."""
        key_str = "".join(seq)

        # Check user trie first for the complete sequence
        node = self._user_tries[Mode.NORMAL]
        for k in seq:
            node = node.children.get(k)
            if node is None:
                break
        if node is not None and node.action_fn is not None:
            actions = node.action_fn(state)
            return actions

        # gg → go to top (or line N)
        if key_str == "gg":
            target = (state.effective_count_a() - 1) if state.count_a > 0 else 0
            return [MoveCursor(target, 0, add_to_jumplist=True)]

        # G → already a single-key binding; wouldn't appear here normally

        # ZZ / ZQ
        if key_str == "ZZ":
            return [SaveBuffer(), QuitEditor()]
        if key_str == "ZQ":
            return [QuitEditor(force=True)]

        # zz, zt, zb
        if key_str == "zz":
            return [ScrollToCursor("middle")]
        if key_str == "zt":
            return [ScrollToCursor("top")]
        if key_str == "zb":
            return [ScrollToCursor("bottom")]

        # Fold commands: zf, zo, zO, zc, za, zR, zM, zd
        if len(seq) == 2 and seq[0] == "z":
            ch = seq[1]
            line, col = self._cursor
            count = state.effective_count()
            if ch == "f":
                # zf: create a fold covering count lines from cursor
                end_line = min(line + max(count, 1) - 1, self._line_count - 1)
                return [CreateFold(line, end_line)]
            if ch in ("o", "O"):
                return [OpenFold(line)]
            if ch == "c":
                return [CloseFold(line)]
            if ch == "a":
                return [ToggleFold(line)]
            if ch == "R":
                return [OpenAllFolds()]
            if ch == "M":
                return [CloseAllFolds()]
            if ch == "d":
                return [DeleteFold(line)]

        # q{reg} — macro record toggle
        if len(seq) == 2 and seq[0] == "q" and seq[1].isalpha():
            if self._recording:
                self._recording = False
                return [StopMacroRecord()]
            self._recording = True
            self._macro_register = seq[1]
            self._macro_buffer = []
            return [StartMacroRecord(seq[1])]

        # @{reg} — play macro; @: repeats last ex command
        if len(seq) == 2 and seq[0] == "@":
            reg = seq[1]
            if reg == ":":
                return [RepeatLastExCommand()]
            reg = reg if reg != "@" else self._last_macro
            self._last_macro = reg
            return [PlayMacro(reg, state.effective_count())]

        # f/F/t/T{char} — find char motion
        if len(seq) == 2 and seq[0] in ("f", "F", "t", "T"):
            if self._document is None:
                return []
            from peovim.modal.motions import move_F, move_f, move_T, move_t

            ch = seq[1]
            line, col = self._cursor
            count = state.effective_count()
            if seq[0] == "f":
                new_line, new_col = move_f(self._document, line, col, ch, count)
            elif seq[0] == "F":
                new_line, new_col = move_F(self._document, line, col, ch, count)
            elif seq[0] == "t":
                new_line, new_col = move_t(self._document, line, col, ch, count)
            else:
                new_line, new_col = move_T(self._document, line, col, ch, count)
            if state.operator is not None:
                # f/F/t/T are all inclusive motions: the char at the destination is included.
                return self._resolve_operator_motion(
                    state, (line, col), (new_line, new_col), "char", motion_end_inclusive=True
                )
            return [MoveCursor(new_line, new_col)]

        # m{char} — set mark
        if len(seq) == 2 and seq[0] == "m":
            name = seq[1]
            if name.isalpha():
                return [SetMark(name)]
            return []

        # '{char} / `{char} — jump to mark
        if len(seq) == 2 and seq[0] in ("'", "`"):
            name = seq[1]
            line_only = seq[0] == "'"
            return [JumpToMark(name, line_only=line_only)]

        # Text object in operator-pending: i{char} or a{char}
        if len(seq) == 2 and seq[0] in ("i", "a") and state.operator is not None:
            return []  # Phase 2: text_objects.py

        # r{char} — replace char at cursor
        if len(seq) == 2 and seq[0] == "r":
            line, col = self._cursor
            ch = seq[1]
            if self._document is not None:
                line_len = len(self._document.get_line(line))
                if col < line_len:
                    return [ReplaceRange(line, col, line, col + 1, ch)]
            return []

        # g-prefixed sequences
        if len(seq) == 2 and seq[0] == "g":
            ch = seq[1]
            if ch == "g":
                target = (state.effective_count_a() - 1) if state.count_a > 0 else 0
                return [MoveCursor(target, 0, add_to_jumplist=True)]
            if ch == "~":
                state.operator = "g~"
                return None  # operator set, still accumulating
            if ch == "u":
                state.operator = "gu"
                return None
            if ch == "U":
                state.operator = "gU"
                return None
            if ch == "v":
                if self._last_visual_selection is None:
                    return []
                mode, anchor, cursor = self._last_visual_selection
                mode_name = {
                    Mode.VISUAL_CHAR: "char",
                    Mode.VISUAL_LINE: "line",
                    Mode.VISUAL_BLOCK: "block",
                }.get(mode)
                if mode_name is None:
                    return []
                return [MoveCursor(anchor[0], anchor[1]), EnterVisualMode(mode_name), MoveCursor(cursor[0], cursor[1])]
            if ch == "*":
                return [SearchWordUnderCursor(whole_word=False, reverse=False)]
            if ch == "#":
                return [SearchWordUnderCursor(whole_word=False, reverse=True)]
            if ch == "e":
                if self._document is not None:
                    from peovim.modal.motions import move_ge

                    line, col = self._cursor
                    return [MoveCursor(*move_ge(self._document, line, col, state.effective_count()))]
                return []
            if ch == "E":
                if self._document is not None:
                    from peovim.modal.motions import move_gE

                    line, col = self._cursor
                    return [MoveCursor(*move_gE(self._document, line, col, state.effective_count()))]
                return []
            if ch == "I":
                return [EnterInsertMode("col_1")]
            if ch == "d":
                # Go to local definition: find FIRST occurrence from top of file
                if self._document is not None:
                    import re as _re

                    line, col = self._cursor
                    text = self._document.get_line(line)
                    # Find the whole word the cursor is on (not just suffix from col)
                    word = None
                    for wm in _re.finditer(r"\w+", text):
                        if wm.start() <= col <= wm.end():
                            word = wm.group()
                            break
                    if word is None:
                        wm = _re.search(r"\w+", text[col:])
                        if wm:
                            word = wm.group()
                    if word:
                        try:
                            from peovim.core.search import compile_pattern

                            pat = compile_pattern(r"\b" + _re.escape(word) + r"\b")
                            # Search forward from top to find first (definition) occurrence
                            for ln in range(line):
                                fm = pat.search(self._document.get_line(ln))
                                if fm:
                                    return [MoveCursor(ln, fm.start(), add_to_jumplist=True)]
                            # Fall back: first occurrence on current line before cursor
                            fm = pat.search(text[:col])
                            if fm:
                                return [MoveCursor(line, fm.start(), add_to_jumplist=True)]
                        except Exception:
                            pass
                return []
            if ch == "f":
                # Go to file under cursor
                if self._document is not None:
                    import re as _re

                    line, col = self._cursor
                    text = self._document.get_line(line)
                    m = _re.search(r"[\w./\\-]+", text[col:])
                    if m:
                        return [OpenBuffer(m.group())]
                return []
            if ch == "t":
                return [NextTab(state.effective_count())]
            if ch == "T":
                return [PrevTab(state.effective_count())]

        # <C-w> window commands
        if len(seq) == 2 and seq[0] == "<C-w>":
            ch = seq[1]
            count = state.effective_count()
            if ch == "h":
                return [FocusWindow("h", count)]
            if ch == "j":
                return [FocusWindow("j", count)]
            if ch == "k":
                return [FocusWindow("k", count)]
            if ch == "l":
                return [FocusWindow("l", count)]
            if ch == "w":
                return [FocusWindow("l", count)]  # cycle forward
            if ch == "s":
                return [SplitWindow("h")]
            if ch == "v":
                return [SplitWindow("v")]
            if ch == "q":
                return [CloseWindow()]
            if ch == "=":
                return [EqualizeWindows()]
            if ch == ">":
                return [ResizeWindow("h", count)]
            if ch == "<":
                return [ResizeWindow("h", -count)]
            if ch == "+":
                return [ResizeWindow("v", count)]
            if ch == "-":
                return [ResizeWindow("v", -count)]
            if ch == "o":
                return [OnlyWindow()]
            return []

        # [( [{ ]) ]} [[ ]]
        if len(seq) == 2 and seq[0] in ("[", "]"):
            from peovim.modal.motions import (
                move_bracket_close_brace,
                move_bracket_close_paren,
                move_bracket_open_brace,
                move_bracket_open_paren,
                move_section_backward,
                move_section_forward,
            )

            line, col = self._cursor
            count = state.effective_count()
            if self._document is None:
                return []
            if seq == ["[", "("]:
                return [MoveCursor(*move_bracket_open_paren(self._document, line, col, count))]
            if seq == ["[", "{"]:
                return [MoveCursor(*move_bracket_open_brace(self._document, line, col, count))]
            if seq == ["]", ")"]:
                return [MoveCursor(*move_bracket_close_paren(self._document, line, col, count))]
            if seq == ["]", "}"]:
                return [MoveCursor(*move_bracket_close_brace(self._document, line, col, count))]
            if seq == ["[", "["]:
                return [MoveCursor(*move_section_backward(self._document, line, col, count), add_to_jumplist=True)]
            if seq == ["]", "]"]:
                return [MoveCursor(*move_section_forward(self._document, line, col, count), add_to_jumplist=True)]

        return None  # no match recognized yet

    def _is_normal_prefix(self, seq: list[str]) -> bool:
        """Return True if seq still has a chance of completing a known sequence."""
        if not seq:
            return False
        first = seq[0]
        # Single-char starters always need one more key
        if len(seq) == 1 and first in _MULTI_KEY_STARTERS:
            return True
        # Z needs one more: ZZ or ZQ
        if len(seq) == 1 and first == "Z":
            return True
        # g needs one more for most sequences; gg, g~, gu, gU, g*, g#, gt, gT
        if len(seq) == 1 and first == "g":
            return True
        # z needs one more: zz, zt, zb
        if len(seq) == 1 and first == "z":
            return True
        # <C-w> needs one more key
        if len(seq) == 1 and first == "<C-w>":
            return True
        # Check user trie: seq is a valid prefix if there's a node with children
        node = self._user_tries[Mode.NORMAL]
        for k in seq:
            node = node.children.get(k)
            if node is None:
                return False
        return bool(node.children)

    def _lookup_key(self, key: str, mode: Mode) -> TrieNode | None:
        user_node = self._user_tries[mode].children.get(key)
        builtin_node = self._builtin_tries[mode].children.get(key)
        # Builtin operators must not be shadowed by user prefix-only nodes.
        # e.g. surround registers ds/cs bindings which put a prefix at 'd' and 'c',
        # but dd/dw/cw etc. must still reach the operator path.
        if (
            builtin_node is not None
            and builtin_node.is_operator
            and user_node is not None
            and user_node.action_fn is None
        ):
            return builtin_node
        if user_node is not None:
            return user_node
        return builtin_node

    # ------------------------------------------------------------------
    # Insert mode
    # ------------------------------------------------------------------

    def _feed_insert(self, key: str, remap: bool = True) -> list[Action]:
        state = self._state
        line, col = self._cursor

        # --- User insert bindings (autopairs, snippets, etc.) ---
        if remap:
            node = self._user_tries[Mode.INSERT].children.get(key)
            if node is not None and node.action_fn is not None:
                actions = node.action_fn(state)
                state.reset()
                return actions

        # Ctrl-r: waiting for register name
        if state.key_buffer == ["<C-r>"]:
            state.key_buffer.clear()
            if key in _REGISTER_CHARS:
                return [PasteRegister(key, before=True, count=1)]
            return []

        match key:
            case "<Esc>" | "<C-c>":
                return [EnterNormalMode()]
            case "<Left>":
                if col > 0:
                    return [MoveCursor(line, col - 1)]
                if line > 0 and self._document is not None:
                    return [MoveCursor(line - 1, len(self._document.get_line(line - 1)))]
                return []
            case "<Right>":
                if self._document is not None:
                    line_text = self._document.get_line(line)
                    if col < len(line_text):
                        return [MoveCursor(line, col + 1)]
                    if line + 1 < self._document.line_count():
                        return [MoveCursor(line + 1, 0)]
                    return []
                return [MoveCursor(line, col + 1)]
            case "<Up>":
                if line > 0:
                    return [MoveCursor(line - 1, col)]
                return []
            case "<Down>":
                if self._document is not None:
                    if line + 1 < self._document.line_count():
                        return [MoveCursor(line + 1, col)]
                    return []
                return [MoveCursor(min(self._line_count - 1, line + 1), col)]
            case "<Home>":
                return [MoveCursor(line, 0)]
            case "<End>":
                if self._document is not None:
                    return [MoveCursor(line, len(self._document.get_line(line)))]
                return []
            case "<BS>":
                bic = self._block_insert_col
                if bic is not None and col <= bic:
                    return []  # can't backspace before block insert column
                if col > 0:
                    return [DeleteRange(line, col - 1, line, col)]
                elif line > 0:
                    return [DeleteRange(line - 1, 0x7FFFFFFF, line, 0)]
                return []
            case "<Del>":
                if self._document is not None:
                    bic = self._block_insert_col
                    line_text = self._document.get_line(line)
                    # In block insert mode only delete within the typed region
                    if (bic is not None and bic <= col < len(line_text)) or (bic is None and col < len(line_text)):
                        return [DeleteRange(line, col, line, col + 1)]
                return []
            case "<C-w>":
                # Delete word backward (like Vim's <C-w> in insert mode)
                if col == 0:
                    if line > 0:
                        return [DeleteRange(line - 1, 0x7FFFFFFF, line, 0)]
                    return []
                if self._document is not None:
                    text = self._document.get_line(line)
                    pos = col
                    # Skip trailing whitespace
                    while pos > 0 and text[pos - 1] == " ":
                        pos -= 1
                    # Skip word characters
                    if pos > 0 and text[pos - 1].isalnum() or (pos > 0 and text[pos - 1] == "_"):
                        while pos > 0 and (text[pos - 1].isalnum() or text[pos - 1] == "_"):
                            pos -= 1
                    elif pos > 0:
                        while pos > 0 and not text[pos - 1].isalnum() and text[pos - 1] != "_" and text[pos - 1] != " ":
                            pos -= 1
                    return [DeleteRange(line, pos, line, col)]
                return [DeleteRange(line, max(0, col - 1), line, col)]
            case "<C-u>":
                return [DeleteRange(line, 0, line, col)]
            case "<CR>":
                return [InsertNewline(line, col)]
            case "<C-r>":
                state.key_buffer = ["<C-r>"]
                return []
            case "<Tab>":
                return [InsertTab(line, col)]
            case "<S-Tab>":
                return [IndentRange(line, line, "out")]
            case "<C-t>":
                return [IndentRange(line, line, "in")]
            case "<C-d>":
                return [IndentRange(line, line, "out")]
            case _:
                if len(key) == 1:
                    return [InsertText(line, col, key)]
                return []

    # ------------------------------------------------------------------
    # Visual mode
    # ------------------------------------------------------------------

    def _feed_visual(self, key: str) -> list[Action]:
        state = self._state
        line, col = self._cursor
        anchor_line, anchor_col = self._visual_anchor

        # --- Multi-key sequence in progress (e.g. gg, ip, aw) ---
        if state.key_buffer:
            # Always allow ESC/C-c to cancel a pending sequence and exit visual mode
            if key in ("<Esc>", "<C-c>"):
                state.key_buffer.clear()
                state.reset()
                return [EnterNormalMode()]

            state.key_buffer.append(key)
            seq = list(state.key_buffer)

            # Check user visual bindings for multi-key sequences first
            node = self._user_tries[Mode.VISUAL_CHAR]
            for k in seq:
                node = node.children.get(k)  # type: ignore[assignment]
                if node is None:
                    break
            if node is not None and node.action_fn is not None:
                state.key_buffer.clear()
                actions = node.action_fn(state)
                state.reset()
                self._remember_visual_selection((line, col))
                return actions + [EnterNormalMode()]
            if node is not None and node.children:
                return []

            if seq == ["g", "g"]:
                state.key_buffer.clear()
                state.count_a = 0
                return [MoveCursor(0, 0)]
            if len(seq) == 2 and seq[0] == "r":
                state.key_buffer.clear()
                state.count_a = 0
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [ReplaceBlock(*bounds, seq[1]), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                if bounds is None:
                    return [EnterNormalMode()]
                return [ReplaceRange(*bounds, seq[1]), EnterNormalMode()]
            if len(seq) == 2 and seq[0] == "g" and seq[1] in ("~", "u", "U"):
                state.key_buffer.clear()
                state.count_a = 0
                mode = {"~": "toggle", "u": "lower", "U": "upper"}[seq[1]]
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [ChangeCaseBlock(*bounds, mode), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                if bounds is None:
                    return [EnterNormalMode()]
                return [ChangeCase(*bounds, mode), EnterNormalMode()]
            # Text object: i{char} or a{char}
            if len(seq) == 2 and seq[0] in ("i", "a"):
                obj_mode = "inner" if seq[0] == "i" else "outer"
                state.key_buffer.clear()
                state.count_a = 0
                if self._document is not None:
                    rng = _resolve_text_object(self._document, line, col, seq[1], obj_mode)
                    if rng is not None:
                        sl, sc, el, ec = rng
                        self._visual_anchor = (sl, sc)
                        return [MoveCursor(el, max(0, ec - 1))]
                return []
            state.key_buffer.clear()
            return []

        # --- Count accumulation ---
        if key.isdigit() and (key != "0" or state.count_a > 0):
            state.count_a = state.count_a * 10 + int(key)
            return []

        count_a_raw = state.count_a
        count = state.effective_count_a()
        state.count_a = 0  # consume count

        # --- User visual bindings (single-key) ---
        node = self._user_tries[Mode.VISUAL_CHAR].children.get(key)
        if node is not None:
            if node.action_fn is None or node.children:
                # Prefix of a longer sequence — buffer it before built-ins like visual r{char}.
                state.key_buffer = [key]
                return []
            actions = node.action_fn(state)
            state.reset()
            return actions + [EnterNormalMode()]

        match key:
            case "<Esc>" | "<C-c>":
                state.reset()
                self._remember_visual_selection((line, col))
                return [EnterNormalMode()]
            case "v":
                state.reset()
                self._remember_visual_selection((line, col))
                return [EnterNormalMode()]
            case "h" | "<Left>":
                if self._document is not None:
                    from peovim.modal.motions import move_h

                    return [MoveCursor(*move_h(self._document, line, col, count))]
                return [MoveCursor(line, max(0, col - count))]
            case "l" | "<Right>":
                if self._document is not None:
                    from peovim.modal.motions import move_l

                    return [MoveCursor(*move_l(self._document, line, col, count))]
                return [MoveCursor(line, col + count)]
            case "j" | "<Down>":
                if self._document is not None:
                    from peovim.modal.motions import move_j

                    return [MoveCursor(*move_j(self._document, line, col, count))]
                return [MoveCursor(min(self._line_count - 1, line + count), col)]
            case "k" | "<Up>":
                if self._document is not None:
                    from peovim.modal.motions import move_k

                    return [MoveCursor(*move_k(self._document, line, col, count))]
                return [MoveCursor(max(0, line - count), col)]
            case "<C-d>":
                return self._visual_scroll_actions(10, line, col)
            case "<C-u>":
                return self._visual_scroll_actions(-10, line, col)
            case "0" | "<Home>":
                return [MoveCursor(line, 0)]
            case "^":
                if self._document is not None:
                    from peovim.modal.motions import move_first_nonblank

                    return [MoveCursor(*move_first_nonblank(self._document, line, col, 1))]
                return []
            case "$" | "<End>":
                if self._document is not None:
                    from peovim.modal.motions import move_line_end

                    return [MoveCursor(*move_line_end(self._document, line, col, count))]
                return [MoveCursor(line, 0x7FFFFFFF)]
            case "w":
                if self._document is not None:
                    from peovim.modal.motions import move_w

                    return [MoveCursor(*move_w(self._document, line, col, count))]
                return []
            case "W":
                if self._document is not None:
                    from peovim.modal.motions import move_W

                    return [MoveCursor(*move_W(self._document, line, col, count))]
                return []
            case "b":
                if self._document is not None:
                    from peovim.modal.motions import move_b

                    return [MoveCursor(*move_b(self._document, line, col, count))]
                return []
            case "B":
                if self._document is not None:
                    from peovim.modal.motions import move_B

                    return [MoveCursor(*move_B(self._document, line, col, count))]
                return []
            case "e":
                if self._document is not None:
                    from peovim.modal.motions import move_e

                    return [MoveCursor(*move_e(self._document, line, col, count))]
                return []
            case "E":
                if self._document is not None:
                    from peovim.modal.motions import move_E

                    return [MoveCursor(*move_E(self._document, line, col, count))]
                return []
            case "G":
                target = count_a_raw - 1 if count_a_raw > 0 else self._line_count - 1
                return [MoveCursor(target, col)]
            case "g":
                state.key_buffer = ["g"]
                return []
            case "r":
                state.key_buffer = ["r"]
                return []
            case "i" | "a":
                # Text object prefix (ip, iw, etc.)
                state.key_buffer = [key]
                return []
            case "o":
                # Swap cursor and anchor
                self._visual_anchor = (line, col)
                return [MoveCursor(anchor_line, anchor_col)]
            case "O":
                if self.mode == Mode.VISUAL_BLOCK:
                    # Swap to the other corner on the current row.
                    self._visual_anchor = (anchor_line, col)
                    return [MoveCursor(line, anchor_col)]
                return []
            case "y":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    reg = state.register
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [YankBlock(*bounds, reg), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                reg = state.register
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [YankRange(*bounds, reg, "char"), EnterNormalMode()]
            case "d" | "x" | "<Del>":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [DeleteBlock(*bounds, register=state.register, save_deleted=True), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [DeleteRange(*bounds, register=state.register, save_deleted=True), EnterNormalMode()]
            case "c":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [
                        CompoundAction(
                            (
                                DeleteBlock(*bounds, register=state.register, save_deleted=True),
                                EnterInsertMode("cursor"),
                            ),
                            "change",
                        )
                    ]
                bounds = self.visual_char_bounds((line, col))
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [
                    CompoundAction(
                        (DeleteRange(*bounds, register=state.register, save_deleted=True), EnterInsertMode("cursor")),
                        "change",
                    )
                ]
            case "I":
                if self.mode == Mode.VISUAL_BLOCK:
                    self._remember_visual_selection((line, col))
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [BeginBlockInsert(bounds[0], bounds[2], bounds[1]), EnterInsertMode("cursor")]
                return []
            case "A":
                if self.mode == Mode.VISUAL_BLOCK:
                    self._remember_visual_selection((line, col))
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [BeginBlockInsert(bounds[0], bounds[2], bounds[3]), EnterInsertMode("cursor")]
                return []
            case "p" | "P":
                if self.mode == Mode.VISUAL_BLOCK:
                    self._remember_visual_selection((line, col))
                    reg = state.register
                    state.reset()
                    return [PasteRegister(reg, before=(key == "P"), count=1), EnterNormalMode()]
                return []
            case "~":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [ChangeCaseBlock(*bounds, "toggle"), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [ChangeCase(*bounds, "toggle"), EnterNormalMode()]
            case "u":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [ChangeCaseBlock(*bounds, "lower"), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [ChangeCase(*bounds, "lower"), EnterNormalMode()]
            case "U":
                self._remember_visual_selection((line, col))
                if self.mode == Mode.VISUAL_BLOCK:
                    bounds = self.visual_block_bounds((line, col))
                    state.reset()
                    if bounds is None:
                        return [EnterNormalMode()]
                    return [ChangeCaseBlock(*bounds, "upper"), EnterNormalMode()]
                bounds = self.visual_char_bounds((line, col))
                state.reset()
                if bounds is None:
                    return [EnterNormalMode()]
                return [ChangeCase(*bounds, "upper"), EnterNormalMode()]
            case ">":
                start_line = min(anchor_line, line)
                end_line = max(anchor_line, line)
                state.reset()
                return [IndentRange(start_line, end_line, "in")]
            case "<":
                start_line = min(anchor_line, line)
                end_line = max(anchor_line, line)
                state.reset()
                return [IndentRange(start_line, end_line, "out")]
            case ":":
                self._remember_visual_selection((line, col))
                return [EnterCommandMode(":", "'<,'>'")]
            case _:
                return []

    # ------------------------------------------------------------------
    # Replace mode
    # ------------------------------------------------------------------

    def _feed_replace(self, key: str) -> list[Action]:
        line, col = self._cursor
        match key:
            case "<Esc>" | "<C-c>":
                return [EnterNormalMode()]
            case "<BS>":
                if col > 0:
                    return [MoveCursor(line, col - 1)]
                return []
            case "<CR>":
                return [InsertNewline(line, col)]
            case _:
                if len(key) == 1:
                    line_len = len(self._document.get_line(line)) if self._document else 0
                    if col < line_len:
                        return [ReplaceRange(line, col, line, col + 1, key), MoveCursor(line, col + 1)]
                    else:
                        return [InsertText(line, col, key), MoveCursor(line, col + 1)]
                return []

    # ------------------------------------------------------------------
    # Binding registration
    # ------------------------------------------------------------------

    def register_binding(
        self,
        mode: Mode,
        keys: str,
        action_fn: ActionFn | None,
        *,
        is_operator: bool = False,
        is_motion: bool = False,
        motion_fn: MotionFn | None = None,
        range_type: str = "char",
        motion_end_exclusive: bool = False,
        motion_end_inclusive: bool = False,
    ) -> None:
        root = self._builtin_tries[mode]
        node = root
        for key in _parse_key_sequence(keys):
            if key not in node.children:
                node.children[key] = TrieNode()
            node = node.children[key]
        node.action_fn = action_fn
        node.is_operator = is_operator
        node.is_motion = is_motion
        node.motion_fn = motion_fn
        node.range_type = range_type
        node.motion_end_exclusive = motion_end_exclusive
        node.motion_end_inclusive = motion_end_inclusive

    def register_user_binding(
        self,
        mode: Mode,
        keys: str,
        action_fn: ActionFn,
        *,
        noremap: bool = True,
    ) -> None:
        root = self._user_tries[mode]
        node = root
        for key in _parse_key_sequence(keys):
            if key not in node.children:
                node.children[key] = TrieNode()
            node = node.children[key]
        node.action_fn = action_fn

    def add_user_binding(
        self,
        mode: Mode,
        keys: str,
        action_fn: ActionFn,
        *,
        noremap: bool = True,
    ) -> None:
        """Alias for register_user_binding (used by BindingRegistry)."""
        self.register_user_binding(mode, keys, action_fn, noremap=noremap)

    def remove_user_binding(self, mode: Mode, keys: str) -> None:
        """Remove a user binding from the trie. Silently ignores missing bindings."""
        root = self._user_tries[mode]
        tokens = _parse_key_sequence(keys)
        # Walk trie collecting (parent, key) pairs so we can prune
        path: list[tuple[object, str]] = []
        node = root
        for token in tokens:
            child = node.children.get(token)
            if child is None:
                return
            path.append((node, token))
            node = child
        # Clear action on leaf
        node.action_fn = None
        # Prune empty nodes bottom-up
        for parent, token in reversed(path):
            child = parent.children.get(token)  # type: ignore[union-attr]
            if child is not None and not child.children and child.action_fn is None:
                del parent.children[token]  # type: ignore[union-attr]
            else:
                break

    # ------------------------------------------------------------------
    # Built-in bindings
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        N = Mode.NORMAL

        # --- Insert mode entry ---
        self._add(N, "i", lambda s: [EnterInsertMode("cursor")])
        self._add(N, "a", lambda s: [EnterInsertMode("after_cursor")])
        self._add(N, "I", lambda s: [EnterInsertMode("line_start")])
        self._add(N, "A", lambda s: [EnterInsertMode("line_end")])
        self._add(N, "o", lambda s: [EnterInsertMode("new_line_below")])
        self._add(N, "O", lambda s: [EnterInsertMode("new_line_above")])
        self._add(N, "R", lambda s: [EnterReplaceMode(single=False)])

        # --- Mode transitions ---
        self._add(N, "v", lambda s: [EnterVisualMode("char")])
        self._add(N, "V", lambda s: [EnterVisualMode("line")])
        self._add(N, "<C-v>", lambda s: [EnterVisualMode("block")])
        self._add(N, ":", lambda s: [EnterCommandMode(":")])
        self._add(N, "/", lambda s: [EnterCommandMode("/")])
        self._add(N, "?", lambda s: [EnterCommandMode("?")])

        # --- Undo / redo ---
        self._add(N, "u", lambda s: [Undo(s.effective_count_a())])
        self._add(N, "<C-r>", lambda s: [Redo(s.effective_count_a())])

        # --- Paste ---
        self._add(N, "p", lambda s: [PasteRegister(s.register, before=False, count=s.effective_count_a())])
        self._add(N, "P", lambda s: [PasteRegister(s.register, before=True, count=s.effective_count_a())])

        # --- Dot repeat ---
        self._add(N, ".", lambda s: [RepeatLastChange(s.effective_count_a())])

        # --- Operators ---
        self._add_op(N, "d")
        self._add_op(N, "y")
        self._add_op(N, "c")
        self._add_op(N, ">")
        self._add_op(N, "<")
        self._add_op(N, "=")
        self._add_op(N, "!")

        # --- Quick delete/change ---
        self._add(N, "Y", lambda s: [YankLine(self._cursor[0], s.effective_count_a(), s.register)])
        self._add(
            N,
            "x",
            lambda s: [
                DeleteRange(
                    self._cursor[0],
                    self._cursor[1],
                    self._cursor[0],
                    self._cursor[1] + s.effective_count_a(),
                    register=s.register,
                    save_deleted=True,
                )
            ],
        )
        self._add(
            N,
            "<Del>",
            lambda s: [
                DeleteRange(
                    self._cursor[0],
                    self._cursor[1],
                    self._cursor[0],
                    self._cursor[1] + s.effective_count_a(),
                    register=s.register,
                    save_deleted=True,
                )
            ],
        )
        self._add(
            N,
            "X",
            lambda s: [
                DeleteRange(
                    self._cursor[0],
                    max(0, self._cursor[1] - s.effective_count_a()),
                    self._cursor[0],
                    self._cursor[1],
                    register=s.register,
                    save_deleted=True,
                )
            ],
        )
        self._add(
            N,
            "s",
            lambda s: [
                DeleteRange(
                    self._cursor[0],
                    self._cursor[1],
                    self._cursor[0],
                    self._cursor[1] + s.effective_count_a(),
                    register=s.register,
                    save_deleted=True,
                ),
                EnterInsertMode("cursor"),
            ],
        )

        # --- Cursor motions ---
        from peovim.modal.motions import (
            move_B,
            move_b,
            move_E,
            move_e,
            move_first_nonblank,
            move_h,
            move_j,
            move_k,
            move_l,
            move_line_end,
            move_line_start,
            move_minus,
            move_paragraph_backward,
            move_paragraph_forward,
            move_plus,
            move_W,
            move_w,
        )

        self._add(
            N,
            "h",
            lambda s: [MoveCursor(self._cursor[0], max(0, self._cursor[1] - s.effective_count()))],
            is_motion=True,
            motion_fn=move_h,
        )
        self._add(
            N,
            "l",
            lambda s: [MoveCursor(self._cursor[0], self._cursor[1] + s.effective_count())],
            is_motion=True,
            motion_fn=move_l,
        )
        self._add(
            N,
            "j",
            lambda s: [MoveCursor(min(self._cursor[0] + s.effective_count(), self._line_count - 1), self._cursor[1])],
            is_motion=True,
            motion_fn=move_j,
            range_type="line",
        )
        self._add(
            N,
            "k",
            lambda s: [MoveCursor(max(0, self._cursor[0] - s.effective_count()), self._cursor[1])],
            is_motion=True,
            motion_fn=move_k,
            range_type="line",
        )
        self._add(N, "0", lambda s: [MoveCursor(self._cursor[0], 0)], is_motion=True, motion_fn=move_line_start)
        self._add(N, "$", lambda s: [MoveCursor(self._cursor[0], 0x7FFFFFFF)], is_motion=True, motion_fn=move_line_end)
        self._add(N, "^", lambda s: [MoveCursor(self._cursor[0], 0)], is_motion=True, motion_fn=move_first_nonblank)
        self._add(
            N,
            "w",
            lambda s: [MoveCursor(*self._motion(move_w, s.effective_count()))],
            is_motion=True,
            motion_fn=move_w,
            motion_end_exclusive=True,
        )
        self._add(
            N,
            "W",
            lambda s: [MoveCursor(*self._motion(move_W, s.effective_count()))],
            is_motion=True,
            motion_fn=move_W,
            motion_end_exclusive=True,
        )
        self._add(
            N,
            "e",
            lambda s: [MoveCursor(*self._motion(move_e, s.effective_count()))],
            is_motion=True,
            motion_fn=move_e,
            motion_end_inclusive=True,
        )
        self._add(
            N,
            "E",
            lambda s: [MoveCursor(*self._motion(move_E, s.effective_count()))],
            is_motion=True,
            motion_fn=move_E,
            motion_end_inclusive=True,
        )
        self._add(
            N, "b", lambda s: [MoveCursor(*self._motion(move_b, s.effective_count()))], is_motion=True, motion_fn=move_b
        )
        self._add(
            N, "B", lambda s: [MoveCursor(*self._motion(move_B, s.effective_count()))], is_motion=True, motion_fn=move_B
        )
        self._add(
            N,
            "{",
            lambda s: [MoveCursor(*self._motion(move_paragraph_backward, s.effective_count()))],
            is_motion=True,
            motion_fn=move_paragraph_backward,
            range_type="line",
        )
        self._add(
            N,
            "}",
            lambda s: [MoveCursor(*self._motion(move_paragraph_forward, s.effective_count()))],
            is_motion=True,
            motion_fn=move_paragraph_forward,
            range_type="line",
        )
        self._add(
            N,
            "+",
            lambda s: [MoveCursor(*self._motion(move_plus, s.effective_count()))],
            is_motion=True,
            motion_fn=move_plus,
            range_type="line",
        )
        self._add(
            N,
            "-",
            lambda s: [MoveCursor(*self._motion(move_minus, s.effective_count()))],
            is_motion=True,
            motion_fn=move_minus,
            range_type="line",
        )
        self._add(
            N,
            "G",
            lambda s: [
                MoveCursor(
                    (s.effective_count_a() - 1) if s.count_a > 0 else (self._line_count - 1),
                    0,
                    add_to_jumplist=True,
                )
            ],
        )

        # --- Search ---
        self._add(N, "n", lambda s: [SearchNext(reverse=False, count=s.effective_count())])
        self._add(N, "N", lambda s: [SearchNext(reverse=True, count=s.effective_count())])
        self._add(N, "*", lambda s: [SearchWordUnderCursor(whole_word=True, reverse=False)])
        self._add(N, "#", lambda s: [SearchWordUnderCursor(whole_word=True, reverse=True)])

        # --- Ctrl-a / Ctrl-x ---
        self._add(N, "<C-a>", lambda s: [IncrementNumber(s.effective_count_a())])
        self._add(N, "<C-x>", lambda s: [IncrementNumber(-s.effective_count_a())])

        # --- Sentence motions ---
        from peovim.modal.motions import move_sentence_backward, move_sentence_forward

        self._add(
            N,
            "(",
            lambda s: [MoveCursor(*self._motion(move_sentence_backward, s.effective_count()))],
            is_motion=True,
            motion_fn=move_sentence_backward,
        )
        self._add(
            N,
            ")",
            lambda s: [MoveCursor(*self._motion(move_sentence_forward, s.effective_count()))],
            is_motion=True,
            motion_fn=move_sentence_forward,
        )

        # --- Scroll ---
        # <A-h/j/k/l> are registered as <Plug>Sidebar* via EditorAPI._register_sidebar_plugs()

        self._add(N, "<C-e>", lambda s: [ScrollView(s.effective_count())])
        self._add(N, "<C-y>", lambda s: [ScrollView(-s.effective_count())])
        self._add(N, "<C-d>", lambda s: [ScrollView(10)])
        self._add(N, "<C-u>", lambda s: [ScrollView(-10)])
        self._add(N, "<C-f>", lambda s: [ScrollView(20)])
        self._add(N, "<C-b>", lambda s: [ScrollView(-20)])

        # --- Buffer lifecycle ---
        self._add(N, "<C-o>", lambda s: [JumpBack(s.effective_count_a())])
        self._add(N, "<C-i>", lambda s: [JumpForward(s.effective_count_a())])

    def _motion(self, motion_fn: MotionFn, count: int) -> tuple[int, int]:
        """Helper: call a motion function with current engine state."""
        if self._document is None:
            line, col = self._cursor
            return (line, col)
        line, col = self._cursor
        return motion_fn(self._document, line, col, count)

    def _add(
        self,
        mode: Mode,
        keys: str,
        fn: ActionFn,
        *,
        is_motion: bool = False,
        motion_fn: MotionFn | None = None,
        range_type: str = "char",
        motion_end_exclusive: bool = False,
        motion_end_inclusive: bool = False,
    ) -> None:
        self.register_binding(
            mode,
            keys,
            fn,
            is_motion=is_motion,
            motion_fn=motion_fn,
            range_type=range_type,
            motion_end_exclusive=motion_end_exclusive,
            motion_end_inclusive=motion_end_inclusive,
        )

    def _add_op(self, mode: Mode, op: str) -> None:
        root = self._builtin_tries[mode]
        node = root
        for key in _parse_key_sequence(op):
            if key not in node.children:
                node.children[key] = TrieNode()
            node = node.children[key]
        node.is_operator = True
        node.action_fn = None


# ---------------------------------------------------------------------------
# Key sequence parser
# ---------------------------------------------------------------------------


def _parse_key_sequence(seq: str) -> list[str]:
    """Parse 'abc<CR><C-r>' into ['a', 'b', 'c', '<CR>', '<C-r>']."""
    keys: list[str] = []
    i = 0
    while i < len(seq):
        if seq[i] == "<":
            end = seq.find(">", i)
            if end != -1:
                keys.append(seq[i : end + 1])
                i = end + 1
                continue
        keys.append(seq[i])
        i += 1
    return keys
