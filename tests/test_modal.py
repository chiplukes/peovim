"""
Modal engine: key sequence parsing, count+register+operator+motion, mode transitions.
"""

from unittest.mock import MagicMock

from peovim.core.editor_state import EditorState
from peovim.modal.actions import (
    BeginBlockInsert,
    ChangeCase,
    ChangeCaseBlock,
    DeleteBlock,
    DeleteRange,
    EnterCommandMode,
    EnterInsertMode,
    EnterNormalMode,
    EnterVisualMode,
    IndentRange,
    InsertNewline,
    InsertText,
    MoveCursor,
    PasteRegister,
    Redo,
    RepeatLastChange,
    ReplaceBlock,
    ReplaceRange,
    RunPlugin,
    SaveBuffer,
    ScrollToCursor,
    ScrollView,
    StartMacroRecord,
    StopMacroRecord,
    Undo,
    YankBlock,
    YankLine,
    YankRange,
)
from peovim.modal.engine import ModalEngine, Mode
from peovim.modal.keybindings import BindingRegistry


def feed(engine: ModalEngine, keys: str) -> list:
    """Feed a sequence of keys, return all actions produced."""
    actions = []
    for key in _parse_key_sequence(keys):
        actions.extend(engine.feed_key(key))
    return actions


def _parse_key_sequence(seq: str) -> list[str]:
    """Parse 'abc<Esc><CR>dd' into individual key strings."""
    keys = []
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


def make_engine() -> ModalEngine:
    return ModalEngine()


# ---------------------------------------------------------------------------
# Mode initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_starts_in_normal_mode(self):
        eng = make_engine()
        assert eng.mode == Mode.NORMAL

    def test_mode_enum_values(self):
        assert Mode.NORMAL.value == "normal"
        assert Mode.INSERT.value == "insert"


# ---------------------------------------------------------------------------
# Normal mode — simple actions
# ---------------------------------------------------------------------------


class TestNormalModeSimple:
    def test_i_enters_insert_mode(self):
        eng = make_engine()
        actions = feed(eng, "i")
        assert any(isinstance(a, EnterInsertMode) for a in actions)
        assert any(a.position == "cursor" for a in actions if isinstance(a, EnterInsertMode))

    def test_a_enters_insert_after_cursor(self):
        eng = make_engine()
        actions = feed(eng, "a")
        assert any(isinstance(a, EnterInsertMode) and a.position == "after_cursor" for a in actions)

    def test_I_inserts_at_line_start(self):
        eng = make_engine()
        actions = feed(eng, "I")
        assert any(isinstance(a, EnterInsertMode) and a.position == "line_start" for a in actions)

    def test_A_inserts_at_line_end(self):
        eng = make_engine()
        actions = feed(eng, "A")
        assert any(isinstance(a, EnterInsertMode) and a.position == "line_end" for a in actions)

    def test_o_opens_line_below(self):
        eng = make_engine()
        actions = feed(eng, "o")
        assert any(isinstance(a, EnterInsertMode) and a.position == "new_line_below" for a in actions)

    def test_O_opens_line_above(self):
        eng = make_engine()
        actions = feed(eng, "O")
        assert any(isinstance(a, EnterInsertMode) and a.position == "new_line_above" for a in actions)

    def test_colon_enters_command_mode(self):
        eng = make_engine()
        actions = feed(eng, ":")
        assert any(isinstance(a, EnterCommandMode) and a.prompt == ":" for a in actions)

    def test_u_undo(self):
        eng = make_engine()
        actions = feed(eng, "u")
        assert any(isinstance(a, Undo) for a in actions)

    def test_ctrl_r_redo(self):
        eng = make_engine()
        actions = feed(eng, "<C-r>")
        assert any(isinstance(a, Redo) for a in actions)

    def test_dot_repeat(self):
        eng = make_engine()
        actions = feed(eng, ".")
        assert any(isinstance(a, RepeatLastChange) for a in actions)

    def test_v_visual_char(self):
        eng = make_engine()
        actions = feed(eng, "v")
        assert any(isinstance(a, EnterVisualMode) and a.mode == "char" for a in actions)

    def test_V_visual_line(self):
        eng = make_engine()
        actions = feed(eng, "V")
        assert any(isinstance(a, EnterVisualMode) and a.mode == "line" for a in actions)

    def test_ctrl_v_visual_block(self):
        eng = make_engine()
        actions = feed(eng, "<C-v>")
        assert any(isinstance(a, EnterVisualMode) and a.mode == "block" for a in actions)

    def test_p_paste_after(self):
        eng = make_engine()
        actions = feed(eng, "p")
        assert any(isinstance(a, PasteRegister) and not a.before for a in actions)

    def test_P_paste_before(self):
        eng = make_engine()
        actions = feed(eng, "P")
        assert any(isinstance(a, PasteRegister) and a.before for a in actions)

    def test_yy_yank_line(self):
        eng = make_engine()
        actions = feed(eng, "yy")
        assert any(isinstance(a, YankLine) for a in actions)

    def test_dd_delete_line(self):
        eng = make_engine()
        actions = feed(eng, "dd")
        assert any(isinstance(a, DeleteRange) for a in actions)

    def test_slash_enters_search(self):
        eng = make_engine()
        actions = feed(eng, "/")
        assert any(isinstance(a, EnterCommandMode) and a.prompt == "/" for a in actions)

    def test_ZZ_save_and_quit(self):
        eng = make_engine()
        actions = feed(eng, "ZZ")
        assert any(isinstance(a, SaveBuffer) for a in actions)

    def test_gg_move_to_top(self):
        eng = make_engine()
        actions = feed(eng, "gg")
        assert any(isinstance(a, MoveCursor) and a.line == 0 for a in actions)


# ---------------------------------------------------------------------------
# Count parsing
# ---------------------------------------------------------------------------


class TestCountParsing:
    def test_count_with_u(self):
        eng = make_engine()
        actions = feed(eng, "3u")
        undo_actions = [a for a in actions if isinstance(a, Undo)]
        assert len(undo_actions) == 1
        assert undo_actions[0].count == 3

    def test_count_with_yy(self):
        eng = make_engine()
        actions = feed(eng, "5yy")
        yank_actions = [a for a in actions if isinstance(a, YankLine)]
        assert len(yank_actions) == 1
        assert yank_actions[0].count == 5

    def test_count_with_dd(self):
        eng = make_engine()
        actions = feed(eng, "3dd")
        delete_actions = [a for a in actions if isinstance(a, DeleteRange)]
        assert len(delete_actions) == 1

    def test_two_digit_count(self):
        eng = make_engine()
        actions = feed(eng, "12u")
        undo_actions = [a for a in actions if isinstance(a, Undo)]
        assert undo_actions[0].count == 12

    def test_zero_alone_is_motion(self):
        # bare '0' should be motion go-to-col-0, NOT a count digit
        eng = make_engine()
        actions = feed(eng, "0")
        # Should produce a MoveCursor to col 0, not accumulate count
        assert any(isinstance(a, MoveCursor) and a.col == 0 for a in actions)

    def test_zero_after_digit_is_count(self):
        eng = make_engine()
        actions = feed(eng, "10u")
        undo_actions = [a for a in actions if isinstance(a, Undo)]
        assert undo_actions[0].count == 10


# ---------------------------------------------------------------------------
# Register prefix
# ---------------------------------------------------------------------------


class TestRegisterPrefix:
    def test_register_before_yy(self):
        eng = make_engine()
        actions = feed(eng, '"ayy')
        yank_actions = [a for a in actions if isinstance(a, YankLine)]
        assert yank_actions[0].register == "a"

    def test_register_before_dd(self):
        eng = make_engine()
        actions = feed(eng, '"bdd')
        # delete goes into register b — engine should record register
        delete_actions = [a for a in actions if isinstance(a, DeleteRange)]
        assert len(delete_actions) == 1
        assert delete_actions[0].register == "b"
        assert delete_actions[0].save_deleted is True

    def test_register_before_p(self):
        eng = make_engine()
        actions = feed(eng, '"ap')
        paste_actions = [a for a in actions if isinstance(a, PasteRegister)]
        assert paste_actions[0].register == "a"


# ---------------------------------------------------------------------------
# Insert mode
# ---------------------------------------------------------------------------


class TestInsertMode:
    def test_esc_returns_to_normal(self):
        eng = make_engine()
        feed(eng, "i")
        assert eng.mode == Mode.INSERT
        actions = feed(eng, "<Esc>")
        assert eng.mode == Mode.NORMAL
        assert any(isinstance(a, EnterNormalMode) for a in actions)

    def test_ctrl_c_returns_to_normal(self):
        eng = make_engine()
        feed(eng, "i")
        feed(eng, "<C-c>")
        assert eng.mode == Mode.NORMAL

    def test_printable_inserts_text(self):
        eng = make_engine()
        feed(eng, "i")
        actions = feed(eng, "h")
        assert any(isinstance(a, InsertText) and a.text == "h" for a in actions)

    def test_cr_inserts_newline(self):
        eng = make_engine()
        feed(eng, "i")
        actions = feed(eng, "<CR>")
        assert any(isinstance(a, InsertNewline) for a in actions)

    def test_bs_deletes(self):
        eng = make_engine()
        eng.set_cursor(0, 3)  # place cursor at col 3 so BS can delete
        feed(eng, "i")
        actions = feed(eng, "<BS>")
        assert any(isinstance(a, DeleteRange) for a in actions)

    def test_insert_mode_left_arrow_moves_cursor(self):
        eng = make_engine()
        eng.set_cursor(0, 3)
        feed(eng, "i")
        actions = feed(eng, "<Left>")
        assert actions == [MoveCursor(0, 2)]

    def test_insert_mode_right_arrow_moves_cursor(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "i")
        actions = feed(eng, "<Right>")
        assert actions == [MoveCursor(0, 2)]

    def test_typing_sequence(self):
        eng = make_engine()
        feed(eng, "i")
        actions = []
        for ch in "abc":
            actions.extend(eng.feed_key(ch))
        texts = [a.text for a in actions if isinstance(a, InsertText)]
        assert texts == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Multi-key sequences
# ---------------------------------------------------------------------------


class TestMultiKeySequences:
    def test_g_g_goes_to_top(self):
        eng = make_engine()
        actions = feed(eng, "gg")
        assert any(isinstance(a, MoveCursor) and a.line == 0 for a in actions)

    def test_q_a_starts_macro(self):
        eng = make_engine()
        actions = feed(eng, "qa")
        assert any(isinstance(a, StartMacroRecord) and a.register == "a" for a in actions)
        assert eng.mode == Mode.NORMAL  # mode unchanged; just recording

    def test_q_in_record_stops(self):
        eng = make_engine()
        feed(eng, "qa")
        actions = feed(eng, "q")
        assert any(isinstance(a, StopMacroRecord) for a in actions)

    def test_zz_scroll_to_middle(self):
        eng = make_engine()
        actions = feed(eng, "zz")
        assert any(isinstance(a, ScrollToCursor) and a.position == "middle" for a in actions)

    def test_zt_scroll_to_top(self):
        eng = make_engine()
        actions = feed(eng, "zt")
        assert any(isinstance(a, ScrollToCursor) and a.position == "top" for a in actions)

    def test_zb_scroll_to_bottom(self):
        eng = make_engine()
        actions = feed(eng, "zb")
        assert any(isinstance(a, ScrollToCursor) and a.position == "bottom" for a in actions)


# ---------------------------------------------------------------------------
# State reset after action
# ---------------------------------------------------------------------------


class TestStateReset:
    def test_state_resets_after_action(self):
        eng = make_engine()
        feed(eng, "3u")
        # Next action should have no count
        actions = feed(eng, "u")
        undo_actions = [a for a in actions if isinstance(a, Undo)]
        assert undo_actions[0].count == 1

    def test_incomplete_sequence_does_not_produce_action(self):
        eng = make_engine()
        # 'g' alone is a prefix — should produce nothing
        actions = eng.feed_key("g")
        assert actions == []

    def test_register_prefix_alone_produces_nothing(self):
        eng = make_engine()
        actions = eng.feed_key('"')
        assert actions == []
        actions = eng.feed_key("a")  # complete register prefix
        assert actions == []  # still waiting for command


# ---------------------------------------------------------------------------
# Bug fixes: visual mode navigation, leader expansion, multi-char user bindings
# ---------------------------------------------------------------------------


class TestVisualModeNavigation:
    def test_visual_enter_sets_anchor(self):
        eng = make_engine()
        eng.set_cursor(3, 5)
        feed(eng, "v")
        assert eng._visual_anchor == (3, 5)

    def test_visual_hjkl_produce_move(self):
        eng = make_engine()
        eng.set_cursor(2, 4)
        feed(eng, "v")
        actions = eng.feed_key("l")
        assert any(isinstance(a, MoveCursor) for a in actions)

    def test_visual_esc_returns_normal(self):
        eng = make_engine()
        feed(eng, "v")
        actions = eng.feed_key("<Esc>")
        assert any(isinstance(a, EnterNormalMode) for a in actions)

    def test_visual_multikey_user_binding_takes_precedence_over_replace(self):
        eng = make_engine()
        eng.register_user_binding(Mode.VISUAL_CHAR, "\\re", lambda _state: [SaveBuffer()])
        feed(eng, "v")

        actions = feed(eng, "\\re")

        assert any(isinstance(a, SaveBuffer) for a in actions)
        assert not any(isinstance(a, ReplaceRange) for a in actions)
        assert eng.mode == Mode.NORMAL

    def test_visual_y_uses_anchor_range(self):
        eng = make_engine()
        eng.set_cursor(0, 0)
        feed(eng, "v")
        eng.set_cursor(0, 4)  # simulate cursor moved to col 4
        actions = eng.feed_key("y")
        yank = next((a for a in actions if isinstance(a, YankRange)), None)
        assert yank is not None
        assert yank.start_col == 0
        assert yank.end_col == 5

    def test_visual_d_uses_anchor_range(self):
        eng = make_engine()
        eng.set_cursor(0, 0)
        feed(eng, "v")
        eng.set_cursor(0, 5)
        actions = eng.feed_key("d")
        delete = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert delete is not None
        assert delete.start_col == 0
        assert delete.end_col == 6

    def test_visual_gg_moves_to_top(self):
        eng = make_engine()
        eng.set_cursor(10, 0)
        eng.set_line_count(20)
        feed(eng, "v")
        actions = feed(eng, "gg")
        assert any(isinstance(a, MoveCursor) and a.line == 0 for a in actions)

    def test_visual_G_moves_to_bottom(self):
        eng = make_engine()
        eng.set_cursor(0, 0)
        eng.set_line_count(15)
        feed(eng, "v")
        actions = eng.feed_key("G")
        assert any(isinstance(a, MoveCursor) and a.line == 14 for a in actions)

    def test_visual_ctrl_d_scrolls_and_moves_cursor(self):
        eng = make_engine()
        eng.set_cursor(5, 3)
        eng.set_line_count(40)
        feed(eng, "v")

        actions = eng.feed_key("<C-d>")

        assert actions == [ScrollView(10), MoveCursor(15, 3)]

    def test_visual_ctrl_u_scrolls_and_moves_cursor(self):
        eng = make_engine()
        eng.set_cursor(15, 2)
        eng.set_line_count(40)
        feed(eng, "v")

        actions = eng.feed_key("<C-u>")

        assert actions == [ScrollView(-10), MoveCursor(5, 2)]

    def test_visual_block_regions_form_rectangle(self):
        eng = make_engine()
        eng.set_cursor(1, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 5)

        regions = eng.visual_selection_regions()

        assert regions == [
            (1, 2, 1, 6),
            (2, 2, 2, 6),
            (3, 2, 3, 6),
        ]

    def test_visual_char_regions_use_exclusive_end(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "v")
        eng.set_cursor(0, 4)

        assert eng.visual_selection_regions() == [(0, 1, 0, 5)]

    def test_visual_line_regions_cover_full_lines(self):
        eng = make_engine()
        eng.set_cursor(2, 3)
        feed(eng, "V")
        eng.set_cursor(4, 1)

        assert eng.visual_selection_regions() == [(2, 0, 4, 0x7FFFFFFF)]

    def test_visual_block_y_emits_block_yank(self):
        eng = make_engine()
        eng.set_cursor(1, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 5)

        actions = eng.feed_key("y")

        yank = next((a for a in actions if isinstance(a, YankBlock)), None)
        assert yank is not None
        assert (yank.start_line, yank.start_col, yank.end_line, yank.end_col) == (1, 2, 3, 6)

    def test_visual_block_d_emits_block_delete(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = eng.feed_key("d")

        delete = next((a for a in actions if isinstance(a, DeleteBlock)), None)
        assert delete is not None
        assert (delete.start_line, delete.start_col, delete.end_line, delete.end_col) == (0, 1, 2, 4)

    def test_visual_block_I_emits_block_insert(self):
        eng = make_engine()
        eng.set_cursor(1, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 4)

        actions = eng.feed_key("I")

        block_insert = next((a for a in actions if isinstance(a, BeginBlockInsert)), None)
        assert block_insert is not None
        assert (block_insert.start_line, block_insert.end_line, block_insert.col) == (1, 3, 1)
        assert any(isinstance(a, EnterInsertMode) for a in actions)

    def test_visual_block_A_emits_block_insert_at_block_edge(self):
        eng = make_engine()
        eng.set_cursor(0, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 5)

        actions = eng.feed_key("A")

        block_insert = next((a for a in actions if isinstance(a, BeginBlockInsert)), None)
        assert block_insert is not None
        assert (block_insert.start_line, block_insert.end_line, block_insert.col) == (0, 2, 6)

    def test_visual_block_p_emits_paste_and_returns_to_normal(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = eng.feed_key("p")

        paste = next((a for a in actions if isinstance(a, PasteRegister)), None)
        assert paste is not None
        assert paste.before is False
        assert any(isinstance(a, EnterNormalMode) for a in actions)

    def test_visual_block_P_emits_paste_before_and_returns_to_normal(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = eng.feed_key("P")

        paste = next((a for a in actions if isinstance(a, PasteRegister)), None)
        assert paste is not None
        assert paste.before is True
        assert any(isinstance(a, EnterNormalMode) for a in actions)

    def test_visual_block_r_emits_block_replace(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = feed(eng, "rZ")

        replace = next((a for a in actions if isinstance(a, ReplaceBlock)), None)
        assert replace is not None
        assert (replace.start_line, replace.start_col, replace.end_line, replace.end_col, replace.char) == (
            0,
            1,
            2,
            4,
            "Z",
        )

    def test_visual_block_tilde_emits_block_case_toggle(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = eng.feed_key("~")

        change = next((a for a in actions if isinstance(a, ChangeCaseBlock)), None)
        assert change is not None
        assert change.mode == "toggle"

    def test_visual_block_gu_emits_block_lower(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 3)

        actions = feed(eng, "gu")

        change = next((a for a in actions if isinstance(a, ChangeCaseBlock)), None)
        assert change is not None
        assert change.mode == "lower"

    def test_visual_tilde_emits_change_case_for_charwise_selection(self):
        eng = make_engine()
        eng.set_cursor(0, 0)
        feed(eng, "v")
        eng.set_cursor(0, 2)

        actions = eng.feed_key("~")

        change = next((a for a in actions if isinstance(a, ChangeCase)), None)
        assert change is not None
        assert change.mode == "toggle"

    def test_visual_block_greater_emits_linewise_indent_range(self):
        eng = make_engine()
        eng.set_cursor(1, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 5)

        actions = eng.feed_key(">")

        indent = next((a for a in actions if isinstance(a, IndentRange)), None)
        assert indent is not None
        assert (indent.start_line, indent.end_line, indent.direction) == (1, 3, "in")
        assert not any(isinstance(a, EnterNormalMode) for a in actions)

    def test_visual_block_less_emits_linewise_outdent_range(self):
        eng = make_engine()
        eng.set_cursor(0, 1)
        feed(eng, "<C-v>")
        eng.set_cursor(2, 4)

        actions = eng.feed_key("<")

        indent = next((a for a in actions if isinstance(a, IndentRange)), None)
        assert indent is not None
        assert (indent.start_line, indent.end_line, indent.direction) == (0, 2, "out")
        assert not any(isinstance(a, EnterNormalMode) for a in actions)

    def test_visual_block_O_swaps_to_other_corner_same_row(self):
        eng = make_engine()
        eng.set_cursor(1, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 5)

        actions = eng.feed_key("O")

        assert actions == [MoveCursor(3, 2)]
        assert eng.visual_selection_regions((3, 2)) == [(1, 2, 1, 6), (2, 2, 2, 6), (3, 2, 3, 6)]

    def test_gv_reselects_last_visual_block_selection(self):
        eng = make_engine()
        eng.set_cursor(1, 2)
        feed(eng, "<C-v>")
        eng.set_cursor(3, 5)
        eng.feed_key("d")

        actions = feed(eng, "gv")

        assert actions == [MoveCursor(1, 2), EnterVisualMode("block"), MoveCursor(3, 5)]


class TestUserMultiKeyBindings:
    """Bug fix: multi-char user bindings like gcc were ignored."""

    def test_three_char_user_binding_fires(self):
        from peovim.modal.engine import Mode

        eng = make_engine()
        fired = []
        eng.add_user_binding(Mode.NORMAL, "gcc", lambda s: fired.append(True) or [])
        feed(eng, "gcc")
        assert fired, "gcc user binding should have fired"

    def test_three_char_prefix_waits(self):
        from peovim.modal.engine import Mode

        eng = make_engine()
        eng.add_user_binding(Mode.NORMAL, "gcc", lambda s: [])
        # After 'g', nothing
        assert eng.feed_key("g") == []
        # After 'gc', still nothing (waiting for third char)
        assert eng.feed_key("c") == []

    def test_two_char_user_binding_fires(self):
        from peovim.modal.engine import Mode

        eng = make_engine()
        fired = []
        eng.add_user_binding(Mode.NORMAL, "gx", lambda s: fired.append(True) or [])
        feed(eng, "gx")
        assert fired


class TestLeaderExpansion:
    """Bug fix: <leader>xx bindings were not matched when leader key is pressed."""

    def test_leader_binding_fires_with_actual_key(self):
        eng = make_engine()
        dispatcher = MagicMock()
        dispatcher._plugin_callbacks = {}
        # Simulate leader = space via editor_state.options
        opts = MagicMock()
        opts.get.return_value = " "
        dispatcher._editor_state.options = opts

        registry = BindingRegistry(eng, dispatcher)
        registry.register("normal", "<leader>f", lambda: None)

        # Pressing space then f should produce a RunPlugin action
        eng.feed_key(" ")
        actions = eng.feed_key("f")
        assert any(isinstance(a, RunPlugin) for a in actions), (
            "leader binding should produce RunPlugin when leader key (space) is pressed"
        )

    def test_leader_key_stored_with_original_keys_for_display(self):
        eng = make_engine()
        dispatcher = MagicMock()
        dispatcher._plugin_callbacks = {}
        opts = MagicMock()
        opts.get.return_value = " "
        dispatcher._editor_state.options = opts

        registry = BindingRegistry(eng, dispatcher)
        registry.register("normal", "<leader>f", lambda: None, desc="Find")
        bindings = registry.get_bindings("normal")
        # Display keys should retain <leader> notation
        assert bindings[0].keys == "<leader>f"

    def test_existing_leader_binding_rebinds_when_leader_changes(self):
        eng = make_engine()
        dispatcher = MagicMock()
        dispatcher._plugin_callbacks = {}
        dispatcher._editor_state = EditorState()

        registry = BindingRegistry(eng, dispatcher)
        registry.register("normal", "<leader>f", lambda: None)

        dispatcher._editor_state.options.set_global("leader", " ")

        assert eng.feed_key("\\") == []
        eng.feed_key(" ")
        actions = eng.feed_key("f")
        assert any(isinstance(a, RunPlugin) for a in actions)

    def test_existing_leader_group_rebinds_when_leader_changes(self):
        dispatcher = MagicMock()
        dispatcher._plugin_callbacks = {}
        dispatcher._editor_state = EditorState()

        registry = BindingRegistry(make_engine(), dispatcher)
        registry.register_group("<leader>f", "Find")

        dispatcher._editor_state.options.set_global("leader", " ")

        assert registry.get_group_name("\\f") == ""
        assert registry.get_group_name(" f") == "Find"


# ---------------------------------------------------------------------------
# f/t/F/T operator inclusivity (dt", df", dF", dT")
# ---------------------------------------------------------------------------


class TestFindCharOperator:
    """dt/df/dF/dT delete ranges must be inclusive of the motion endpoint."""

    def _engine_with_doc(self, text: str) -> "ModalEngine":
        from peovim.core.document import Document

        eng = make_engine()
        doc = Document()
        doc.load_string(text)
        eng.set_document(doc)
        eng.set_cursor(0, 1)  # cursor on 'h' (col 1) in '"hello"'
        return eng

    def test_dt_quote_deletes_to_but_not_including_quote(self):
        # "hello" cursor on h (col 1), dt" should delete hello leaving ""
        eng = self._engine_with_doc('"hello"')
        actions = feed(eng, 'dt"')
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None
        assert dr.start_col == 1
        assert dr.end_col == 6  # exclusive end = col of '"' (col 6) → deletes hello

    def test_df_quote_deletes_through_quote(self):
        # "hello" cursor on h (col 1), df" should delete hello" leaving "
        eng = self._engine_with_doc('"hello"')
        actions = feed(eng, 'df"')
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None
        assert dr.start_col == 1
        assert dr.end_col == 7  # exclusive end = col 7 → deletes hello" (cols 1-6)

    def test_dF_quote_deletes_backward_through_quote(self):
        # "hello" cursor on o (col 5), dF" should delete "hell leaving o"
        from peovim.core.document import Document

        eng = make_engine()
        doc = Document()
        doc.load_string('"hello"')
        eng.set_document(doc)
        eng.set_cursor(0, 5)  # cursor on 'o'
        actions = feed(eng, 'dF"')
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None
        assert dr.start_col == 0  # includes the '"' at col 0
        assert dr.end_col == 6  # exclusive end = col of 'o' + 1

    def test_dT_quote_deletes_backward_exclusive_of_quote(self):
        # "hello" cursor on o (col 5), dT" should delete hell leaving "o"
        from peovim.core.document import Document

        eng = make_engine()
        doc = Document()
        doc.load_string('"hello"')
        eng.set_document(doc)
        eng.set_cursor(0, 5)  # cursor on 'o'
        actions = feed(eng, 'dT"')
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None
        assert dr.start_col == 1  # one after '"' at col 0
        assert dr.end_col == 6  # exclusive end = col of 'o' + 1


# ---------------------------------------------------------------------------
# Operator + motion: user binding shadowing builtin motion
# ---------------------------------------------------------------------------


class TestOperatorMotionWithUserBinding:
    def _engine_with_doc(self, text: str, col: int = 0) -> ModalEngine:
        from peovim.core.document import Document

        eng = make_engine()
        doc = Document()
        doc.load_string(text)
        eng.set_document(doc)
        eng.set_cursor(0, col)
        return eng

    def test_de_works_when_e_is_shadowed_by_user_nmap(self):
        # Regression: dashboard maps 'e' as a non-motion user binding.
        # de should still use the builtin e motion and produce a DeleteRange.
        eng = self._engine_with_doc("hello world")
        eng.register_user_binding(Mode.NORMAL, "e", lambda s: [])
        actions = feed(eng, "de")
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None, "de should delete even when 'e' is a non-motion user binding"
        assert dr.start_col == 0
        assert dr.end_col == 5  # inclusive end of 'hello' + 1

    def test_dw_works_when_w_is_shadowed_by_user_nmap(self):
        eng = self._engine_with_doc("hello world")
        eng.register_user_binding(Mode.NORMAL, "w", lambda s: [])
        actions = feed(eng, "dw")
        dr = next((a for a in actions if isinstance(a, DeleteRange)), None)
        assert dr is not None, "dw should delete even when 'w' is a non-motion user binding"
