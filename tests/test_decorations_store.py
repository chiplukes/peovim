"""Tests for peovim.core.decorations_store.DecorationsStore"""

from peovim.core.decorations_store import DecorationsStore
from peovim.core.style import Style
from peovim.ui.decorations import HighlightRegion, Sign

_STYLE = Style(fg=(255, 0, 0))


def _make_sign(line: int = 0) -> Sign:
    return Sign(line=line, char=">", style=_STYLE, priority=0)


def _make_hr(line: int = 0) -> HighlightRegion:
    return HighlightRegion(start_line=line, start_col=0, end_line=line, end_col=5, style=_STYLE)


def test_add_returns_id():
    store = DecorationsStore()
    dec_id = store.add(1, "ns", _make_hr())
    assert isinstance(dec_id, int)


def test_add_multiple_unique_ids():
    store = DecorationsStore()
    id1 = store.add(1, "ns", _make_hr())
    id2 = store.add(1, "ns", _make_hr())
    id3 = store.add(1, "ns2", _make_hr())
    assert len({id1, id2, id3}) == 3


def test_get_for_buffer_empty():
    store = DecorationsStore()
    assert store.get_for_buffer(99) == []


def test_get_for_buffer_returns_decorations():
    store = DecorationsStore()
    hr = _make_hr()
    store.add(1, "ns", hr)
    result = store.get_for_buffer(1)
    assert hr in result


def test_clear_namespace():
    store = DecorationsStore()
    hr1 = _make_hr(0)
    hr2 = _make_hr(1)
    store.add(1, "ns_a", hr1)
    store.add(1, "ns_b", hr2)
    store.clear_namespace(1, "ns_a")
    result = store.get_for_buffer(1)
    assert hr1 not in result
    assert hr2 in result


def test_clear_buffer():
    store = DecorationsStore()
    hr1 = _make_hr()
    hr2 = _make_hr()
    store.add(1, "ns_a", hr1)
    store.add(1, "ns_b", hr2)
    store.add(2, "ns_a", _make_hr())
    store.clear_buffer(1)
    assert store.get_for_buffer(1) == []
    assert len(store.get_for_buffer(2)) == 1


def test_remove_by_id():
    store = DecorationsStore()
    hr = _make_hr()
    dec_id = store.add(1, "ns", hr)
    store.remove(1, "ns", dec_id)
    assert store.get_for_buffer(1) == []


def test_list_namespaces():
    store = DecorationsStore()
    store.add(1, "zzz", _make_hr())
    store.add(1, "aaa", _make_hr())
    store.add(2, "other", _make_hr())
    ns = store.list_namespaces(1)
    assert ns == ["aaa", "zzz"]


def test_list_namespaces_empty():
    store = DecorationsStore()
    assert store.list_namespaces(999) == []


def test_has_signs_false():
    store = DecorationsStore()
    store.add(1, "ns", _make_hr())
    assert store.has_signs(1) is False


def test_has_signs_true():
    store = DecorationsStore()
    store.add(1, "ns", _make_sign())
    assert store.has_signs(1) is True


def test_get_for_buffer_multiple_ns():
    store = DecorationsStore()
    hr1 = _make_hr(0)
    hr2 = _make_hr(1)
    store.add(1, "ns_a", hr1)
    store.add(1, "ns_b", hr2)
    result = store.get_for_buffer(1)
    assert hr1 in result
    assert hr2 in result
    assert len(result) == 2


def test_two_buffers_isolated():
    store = DecorationsStore()
    hr1 = _make_hr()
    hr2 = _make_hr()
    store.add(1, "ns", hr1)
    store.add(2, "ns", hr2)
    assert store.get_for_buffer(1) == [hr1]
    assert store.get_for_buffer(2) == [hr2]
