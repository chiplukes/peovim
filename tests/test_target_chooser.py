"""TargetChooser — lettered-badge chooser for arbitrary jump targets."""

from __future__ import annotations

from unittest.mock import MagicMock

from peovim.ui.layout import Rect
from peovim.ui.target_chooser import ChooserTarget, TargetChooser


def _make_api():
    api = MagicMock()
    handles: list = []
    api.ui.open_float.side_effect = lambda *a, **k: handles.append(MagicMock()) or handles[-1]
    api._handles = handles
    return api


class TestTargetChooser:
    def test_opens_badge_per_target_and_activates_on_label_key(self):
        api = _make_api()
        act_a, act_b = MagicMock(), MagicMock()
        targets = [
            ChooserTarget(name="wA", rect=Rect(0, 0, 40, 10), activate=act_a),
            ChooserTarget(name="wB", rect=Rect(40, 0, 40, 10), activate=act_b),
        ]
        chooser = TargetChooser(api, targets, hint_title="Go to")

        assert chooser.is_active
        assert api.ui.open_float.call_count == 2
        # Badge content is [[(letter, style)]] — first segment's text is the letter.
        letters = [call.args[0][0][0][0] for call in api.ui.open_float.call_args_list]
        assert letters == ["A", "B"]
        for call in api.ui.open_float.call_args_list:
            assert call.kwargs["width"] == 3 and call.kwargs["height"] == 3
            assert call.kwargs["border"] is True
            assert call.kwargs["focusable"] is False
            assert call.kwargs["z_order"] == 10
        api.push_key_interceptor.assert_called_once_with(chooser)

        consumed = chooser.feed_key("a")  # lowercase

        assert consumed is True
        assert not chooser.is_active
        act_a.assert_called_once_with()
        act_b.assert_not_called()
        assert all(h.close.called for h in api._handles)
        api.pop_key_interceptor.assert_called_once_with(chooser)

    def test_uppercase_label_selects_same_target_as_lowercase(self):
        api = _make_api()
        act_a, act_b = MagicMock(), MagicMock()
        targets = [
            ChooserTarget(name="wA", rect=Rect(0, 0, 40, 10), activate=act_a),
            ChooserTarget(name="wB", rect=Rect(40, 0, 40, 10), activate=act_b),
        ]
        chooser = TargetChooser(api, targets)

        chooser.feed_key("B")

        act_b.assert_called_once_with()
        act_a.assert_not_called()

    def test_escape_cancels_without_activating(self):
        api = _make_api()
        act_a, act_b = MagicMock(), MagicMock()
        targets = [
            ChooserTarget(name="wA", rect=Rect(0, 0, 40, 10), activate=act_a),
            ChooserTarget(name="wB", rect=Rect(40, 0, 40, 10), activate=act_b),
        ]
        chooser = TargetChooser(api, targets)

        consumed = chooser.feed_key("<Esc>")

        assert consumed is True
        assert not chooser.is_active
        act_a.assert_not_called()
        act_b.assert_not_called()
        assert all(h.close.called for h in api._handles)
        api.pop_key_interceptor.assert_called_once_with(chooser)

    def test_unknown_key_cancels_without_activating(self):
        api = _make_api()
        act_a = MagicMock()
        targets = [ChooserTarget(name="wA", rect=Rect(0, 0, 40, 10), activate=act_a)]
        chooser = TargetChooser(api, targets)

        chooser.feed_key("z")

        assert not chooser.is_active
        act_a.assert_not_called()
        api.pop_key_interceptor.assert_called_once_with(chooser)

    def test_missing_rect_skips_badge_but_target_still_selectable(self):
        api = _make_api()
        act = MagicMock()
        targets = [ChooserTarget(name="wA", rect=None, activate=act)]
        chooser = TargetChooser(api, targets)

        assert chooser.is_active
        api.ui.open_float.assert_not_called()  # no rect → no badge
        api.push_key_interceptor.assert_called_once_with(chooser)

        chooser.feed_key("a")

        act.assert_called_once_with()
        api.pop_key_interceptor.assert_called_once_with(chooser)
