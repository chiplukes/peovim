"""Tests for peovim.core.event_bus.EventBus"""

import logging

from peovim.core.event_bus import EventBus


def test_on_emit_basic():
    bus = EventBus()
    calls = []
    bus.on("test", lambda: calls.append(1))
    bus.emit("test")
    assert calls == [1]


def test_on_returns_token():
    bus = EventBus()
    token = bus.on("test", lambda: None)
    assert isinstance(token, int)


def test_off_unsubscribes():
    bus = EventBus()
    calls = []
    token = bus.on("test", lambda: calls.append(1))
    bus.off(token)
    bus.emit("test")
    assert calls == []


def test_once_fires_once():
    bus = EventBus()
    calls = []
    bus.on("test", lambda: calls.append(1), once=True)
    bus.emit("test")
    bus.emit("test")
    assert calls == [1]


def test_once_method():
    bus = EventBus()
    calls = []
    bus.once("test", lambda: calls.append(1))
    bus.emit("test")
    bus.emit("test")
    assert calls == [1]


def test_multiple_handlers():
    bus = EventBus()
    calls = []
    bus.on("test", lambda: calls.append("a"))
    bus.on("test", lambda: calls.append("b"))
    bus.emit("test")
    assert sorted(calls) == ["a", "b"]


def test_emit_kwargs():
    bus = EventBus()
    received = {}
    bus.on("cursor_moved", lambda buf_id, line, col: received.update({"buf_id": buf_id, "line": line, "col": col}))
    bus.emit("cursor_moved", buf_id=42, line=5, col=3)
    assert received == {"buf_id": 42, "line": 5, "col": 3}


def test_emit_unknown_event():
    bus = EventBus()
    # Should not raise
    bus.emit("nonexistent_event", foo="bar")


def test_handler_exception_does_not_crash():
    bus = EventBus()
    calls = []

    def bad_handler():
        raise ValueError("boom")

    def good_handler():
        calls.append(1)

    bus.on("test", bad_handler)
    bus.on("test", good_handler)
    bus.emit("test")  # Should not raise
    assert calls == [1]


def test_handler_exception_is_logged(caplog):
    bus = EventBus()

    def bad_handler():
        raise ValueError("boom")

    bus.on("test", bad_handler)

    with caplog.at_level(logging.ERROR, logger="peovim.event_bus"):
        bus.emit("test")

    assert any("handler for 'test' raised" in message for message in caplog.messages)


def test_off_unknown_token_noop():
    bus = EventBus()
    # Should not raise
    bus.off(99999)


def test_handler_count():
    bus = EventBus()
    assert bus.handler_count("test") == 0
    bus.on("test", lambda: None)
    assert bus.handler_count("test") == 1
    bus.on("test", lambda: None)
    assert bus.handler_count("test") == 2


def test_emit_after_off():
    bus = EventBus()
    calls = []
    token = bus.on("test", lambda: calls.append(1))
    bus.off(token)
    bus.emit("test")
    assert calls == []


def test_multiple_events():
    bus = EventBus()
    a_calls = []
    b_calls = []
    bus.on("event_a", lambda: a_calls.append(1))
    bus.on("event_b", lambda: b_calls.append(2))
    bus.emit("event_a")
    assert a_calls == [1]
    assert b_calls == []
    bus.emit("event_b")
    assert b_calls == [2]
    assert len(a_calls) == 1
