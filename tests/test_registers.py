"""
Register read/write, clipboard, special registers.
"""

from peovim.core.registers import RegisterStore


class TestNamedRegisters:
    def test_set_and_get(self):
        rs = RegisterStore()
        rs.set("a", "hello", "char")
        text, kind = rs.get("a")
        assert text == "hello"
        assert kind == "char"

    def test_uppercase_appends(self):
        rs = RegisterStore()
        rs.set("a", "hello", "char")
        rs.set("A", " world", "char")  # uppercase = append
        text, _ = rs.get("a")
        assert text == "hello world"

    def test_unnamed_register(self):
        rs = RegisterStore()
        rs.set('"', "content", "char")
        text, _ = rs.get('"')
        assert text == "content"

    def test_black_hole_discards(self):
        rs = RegisterStore()
        rs.set("_", "anything", "char")
        text, _ = rs.get("_")
        assert text == ""

    def test_numbered_register(self):
        rs = RegisterStore()
        rs.set("0", "yanked", "line")
        text, kind = rs.get("0")
        assert text == "yanked"
        assert kind == "line"

    def test_get_nonexistent(self):
        rs = RegisterStore()
        text, kind = rs.get("z")
        assert text == ""

    def test_register_types(self):
        rs = RegisterStore()
        rs.set("a", "line content", "line")
        _, kind = rs.get("a")
        assert kind == "line"

    def test_block_register_type(self):
        rs = RegisterStore()
        rs.set("b", "col\ncol", "block")
        _, kind = rs.get("b")
        assert kind == "block"


class TestClipboardRegister:
    def test_clipboard_write_failure_returns_cached_yank(self, monkeypatch):
        """Intermittent bug: when OpenClipboard is locked by another process,
        the write fails silently but old clipboard content remains. get('+') must
        return the cached yanked text, not the stale clipboard content."""
        rs = RegisterStore()
        monkeypatch.setattr(rs, "_write_clipboard", lambda text: False)
        monkeypatch.setattr(rs, "_read_clipboard", lambda: "old clipboard content")
        rs.set("+", "yanked word", "char")
        text, kind = rs.get("+")
        assert text == "yanked word"
        assert kind == "char"

    def test_clipboard_write_success_preserves_kind(self, monkeypatch):
        """When clipboard write succeeds and clipboard matches, kind from yank is preserved."""
        rs = RegisterStore()
        monkeypatch.setattr(rs, "_write_clipboard", lambda text: True)
        monkeypatch.setattr(rs, "_read_clipboard", lambda: "full line")
        rs.set("+", "full line", "line")
        text, kind = rs.get("+")
        assert text == "full line"
        assert kind == "line"

    def test_clipboard_write_success_external_change_uses_clipboard(self, monkeypatch):
        """When write succeeds but another app later changes clipboard, return clipboard content."""
        rs = RegisterStore()
        monkeypatch.setattr(rs, "_write_clipboard", lambda text: True)
        monkeypatch.setattr(rs, "_read_clipboard", lambda: "from browser")
        rs.set("+", "yanked word", "char")
        text, kind = rs.get("+")
        assert text == "from browser"
        assert kind == "char"

    def test_clipboard_empty_falls_back_to_cache(self, monkeypatch):
        """When clipboard is empty (write may have cleared it), use cached value."""
        rs = RegisterStore()
        monkeypatch.setattr(rs, "_write_clipboard", lambda text: True)
        monkeypatch.setattr(rs, "_read_clipboard", lambda: "")
        rs.set("+", "yanked word", "char")
        text, kind = rs.get("+")
        assert text == "yanked word"
        assert kind == "char"


class TestSpecialRegisters:
    def test_read_only_registers(self):
        rs = RegisterStore()
        # Writing to read-only registers is silently ignored
        rs.set(".", "attempt", "char")  # last-insert is read-only
        rs.set("%", "attempt", "char")  # filename is read-only

    def test_set_search_register(self):
        rs = RegisterStore()
        rs.set("/", "pattern", "char")
        text, _ = rs.get("/")
        assert text == "pattern"

    def test_set_command_register(self):
        rs = RegisterStore()
        rs.set(":", "w", "char")
        text, _ = rs.get(":")
        assert text == "w"


class TestNumberedShift:
    def test_shift_on_delete(self):
        """When a new text is stored in '1', old '1' shifts to '2', etc."""
        rs = RegisterStore()
        rs.shift_numbered("first")
        rs.shift_numbered("second")
        text, _ = rs.get("1")
        assert text == "second"
        text, _ = rs.get("2")
        assert text == "first"

    def test_shift_drops_oldest(self):
        rs = RegisterStore()
        for i in range(10):
            rs.shift_numbered(str(i))
        # After 10 shifts (values "0".."9"):
        # "1" has the newest ("9"), "9" has the 9th-most-recent ("1")
        # "0" has been evicted (only 9 slots "1"-"9")
        text, _ = rs.get("1")
        assert text == "9"  # newest
        text, _ = rs.get("9")
        assert text == "1"  # oldest still kept ("0" was evicted)


class TestListRegisters:
    def test_list_returns_set_registers(self):
        rs = RegisterStore()
        rs.set("a", "foo", "char")
        rs.set("b", "bar", "line")
        listing = rs.list_registers()
        assert "a" in listing
        assert "b" in listing

    def test_list_excludes_empty(self):
        rs = RegisterStore()
        rs.set("a", "foo", "char")
        listing = rs.list_registers()
        assert "z" not in listing
