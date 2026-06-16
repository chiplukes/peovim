"""
flash — s + 2-char jump with label overlay.

Press s, type 2 chars, all visible matches get labels;
press the label key(s) to jump there.

Labels are single chars (a-z) when matches <= 26.
When matches exceed 26, all labels become 2-char pairs (aa, ab, ...).
Pressing the first char of a 2-char label updates the overlay to show
only the second char of remaining matches (like flash.nvim).

Labels are per-window (keyed by id(win)) so shared-document splits
never show the same label character.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

_LABELS = "asdfghjklqwertyuiopzxcvbnm"

if TYPE_CHECKING:
    from peovim.api.editor import EditorAPI


def _make_labels(n: int) -> list[str]:
    """Generate n unique labels. Single-char if n<=26, 2-char otherwise."""
    if n <= len(_LABELS):
        return list(_LABELS[:n])
    labels: list[str] = []
    for c1 in _LABELS:
        for c2 in _LABELS:
            labels.append(c1 + c2)
            if len(labels) == n:
                return labels
    return labels


class _FlashState:
    IDLE = "idle"
    CHAR1 = "char1"
    CHAR2 = "char2"
    LABEL = "label"
    SUBLABEL = "sublabel"  # waiting for 2nd char of a 2-char label


class FlashPlugin:  # cm:4d1b6e
    def __init__(self, api: EditorAPI) -> None:
        self._api = api
        self._state = _FlashState.IDLE
        self._char1 = ""
        self._char2 = ""
        self._labels: dict[str, tuple] = {}  # label -> (win, line, col)
        self._label_prefix = ""  # first char pressed in SUBLABEL state
        self._resume_visual_mode: Any = None
        self._resume_visual_anchor: tuple[int, int] | None = None
        self._resume_visual_cursor: tuple[int, int] | None = None
        self._resume_visual_scroll: int | None = None
        self._resume_visual_window: Any = None

    @property
    def is_active(self) -> bool:
        return self._state != _FlashState.IDLE

    def start(self, ctx: Any | None = None) -> None:
        self._remember_visual_state(ctx)
        self._state = _FlashState.CHAR1
        self._char1 = ""
        self._char2 = ""
        self._labels.clear()
        self._label_prefix = ""
        self._api.set_status("flash> ", notify=False)

    def feed_key(self, key: str) -> bool:
        """Returns True if key was consumed by flash."""
        if self._state == _FlashState.IDLE:
            return False

        if key in ("<Esc>", "<C-c>"):
            self._cancel()
            return True

        if self._state == _FlashState.CHAR1:
            if len(key) == 1:
                self._char1 = key
                self._state = _FlashState.CHAR2
                self._api.set_status(f"flash> {key}", notify=False)
            return True

        if self._state == _FlashState.CHAR2:
            if len(key) == 1:
                self._char2 = key
                self._compute_matches()
                if self._labels:
                    self._state = _FlashState.LABEL
                else:
                    self._cancel()
                    self._api.set_status("flash: no matches", notify=False)
            return True

        if self._state == _FlashState.LABEL:
            if len(key) != 1:
                self._cancel()
                return True
            # Check if any label is exactly this key (single-char labels)
            if key in self._labels:
                self._jump(key)
                return True
            # Check if key is a prefix of some 2-char label
            matching = {lbl: tgt for lbl, tgt in self._labels.items() if lbl.startswith(key)}
            if matching:
                self._label_prefix = key
                self._update_sublabel_display(matching)
                self._state = _FlashState.SUBLABEL
            else:
                self._cancel()
            return True

        if self._state == _FlashState.SUBLABEL:
            if len(key) != 1:
                self._cancel()
                return True
            full_label = self._label_prefix + key
            if full_label in self._labels:
                self._jump(full_label)
            else:
                self._cancel()
            return True

        return False

    def _collect_matches(self) -> list[tuple[Any, int, int]]:
        """Collect (win, line, col) for all visible matches across all windows."""
        query = (self._char1 + self._char2).lower()
        active_win = self._api.active_window()
        if self._resume_visual_window is not None:
            all_wins = [self._resume_visual_window]
        else:
            all_wins = [active_win] + [win for win in self._api.list_windows() if win.win_id != active_win.win_id]

        matches: list[tuple[Any, int, int]] = []
        for win in all_wins:
            buf = win.buffer()
            visible_start, visible_end = win.visible_range()
            for ln in range(visible_start, visible_end + 1):
                line_text = buf.get_line(ln).lower()
                search_col = 0
                while True:
                    idx = line_text.find(query, search_col)
                    if idx == -1:
                        break
                    matches.append((win, ln, idx))
                    search_col = idx + 1
        return matches

    def _compute_matches(self) -> None:
        from peovim.core.style import Style
        from peovim.ui.decorations import OverlayChar

        self._labels.clear()
        # Clear old overlays from all windows (per-window keys)
        self._clear_all_flash_labels()

        matches = self._collect_matches()
        if not matches:
            return

        label_list = _make_labels(len(matches))
        label_style = Style(fg=(255, 215, 0), bg=(30, 25, 0))

        for i, (win, ln, col) in enumerate(matches):
            if i >= len(label_list):
                break
            label = label_list[i]
            self._labels[label] = (win, ln, col)
            self._api.add_window_overlay(
                win, "flash:labels", OverlayChar(line=ln, col=col, display_char=label[0], style=label_style)
            )

    def _update_sublabel_display(self, remaining: dict[str, tuple]) -> None:
        """After user presses first char of 2-char label, update overlays to show second char."""
        from peovim.core.style import Style
        from peovim.ui.decorations import OverlayChar

        self._clear_all_flash_labels()

        sublabel_style = Style(fg=(255, 140, 0), bg=(40, 20, 0))
        for full_label, (win, ln, col) in remaining.items():
            second_char = full_label[1] if len(full_label) > 1 else full_label[0]
            self._api.add_window_overlay(
                win,
                "flash:labels",
                OverlayChar(line=ln, col=col, display_char=second_char, style=sublabel_style),
            )
        self._api.set_status(f"flash> {self._char1}{self._char2} → {self._label_prefix}", notify=False)

    def _clear_all_flash_labels(self) -> None:
        """Clear flash label decorations from all windows."""
        for win in self._api.list_windows():
            self._api.clear_window_namespace(win, "flash:labels")

    def _jump(self, label: str) -> None:
        win, line, col = self._labels[label]
        self._api.record_jump()
        if self._resume_visual_mode is not None:
            self._restore_visual_state((line, col))
        else:
            self._api.activate_window(win)
            win.set_cursor(line, col)
            win.scroll_to_cursor()
        self._cancel(restore_visual=False)

    def _cancel(self, *, restore_visual: bool = True) -> None:
        if restore_visual and self._resume_visual_mode is not None and self._resume_visual_cursor is not None:
            self._restore_visual_state(self._resume_visual_cursor, scroll_line=self._resume_visual_scroll)
        self._state = _FlashState.IDLE
        self._label_prefix = ""
        self._clear_all_flash_labels()
        self._clear_visual_resume_state()
        self._api.set_status("", notify=False)

    def _remember_visual_state(self, ctx: Any | None = None) -> None:
        from peovim.modal.engine import Mode

        modal = self._api.modal
        visual_modes = {
            Mode.VISUAL_CHAR,
            Mode.VISUAL_LINE,
            Mode.VISUAL_BLOCK,
        }
        visual_mode_names = {mode.value for mode in visual_modes}
        if modal.mode() not in visual_modes and getattr(ctx, "mode", None) not in visual_mode_names:
            self._clear_visual_resume_state()
            return
        active_win = self._api.active_window()
        if modal.mode() in visual_modes:
            self._resume_visual_mode = modal.mode()
        else:
            self._resume_visual_mode = next(mode for mode in visual_modes if mode.value == ctx.mode)
        self._resume_visual_anchor = modal.visual_anchor()
        self._resume_visual_cursor = active_win.cursor
        self._resume_visual_scroll = active_win.visible_range()[0]
        self._resume_visual_window = active_win

    def _restore_visual_state(
        self,
        cursor: tuple[int, int],
        *,
        scroll_line: int | None = None,
    ) -> None:
        if self._resume_visual_mode is None or self._resume_visual_anchor is None or self._resume_visual_window is None:
            return
        self._api.activate_window(self._resume_visual_window)
        self._resume_visual_window.set_cursor(*cursor)
        if scroll_line is None:
            self._resume_visual_window.scroll_to_cursor()
        else:
            self._resume_visual_window.set_scroll_line(scroll_line)
        modal = self._api.modal
        modal.set_mode(self._resume_visual_mode)
        modal.set_visual_anchor(*self._resume_visual_anchor)

    def _clear_visual_resume_state(self) -> None:
        self._resume_visual_mode = None
        self._resume_visual_anchor = None
        self._resume_visual_cursor = None
        self._resume_visual_scroll = None
        self._resume_visual_window = None


def setup(api: Any) -> None:
    flash = FlashPlugin(api)
    api.register_flash_plugin(flash)

    api.keymap.nmap("<Plug>FlashJump", lambda ctx: flash.start(ctx), desc="Flash: jump")
    api.keymap.nmap("s", "<Plug>FlashJump", desc="Flash jump")
    api.keymap.vmap("s", "<Plug>FlashJump", desc="Flash jump")
