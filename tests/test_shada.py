"""Phase 7a — ShadaStore tests"""

from pathlib import Path

import pytest

from peovim.core.shada import ShadaStore, _merge_history

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_shada(tmp_path):
    """Return a ShadaStore backed by a tmp file."""
    return ShadaStore(path=tmp_path / "shada")


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------


class TestShadaRoundTrip:
    def test_write_creates_file(self, tmp_shada, tmp_path):
        tmp_shada.write()
        assert (tmp_path / "shada").exists()

    def test_missing_file_is_empty_state(self, tmp_path):
        store = ShadaStore(path=tmp_path / "nonexistent_shada")
        store.read()  # should not raise
        assert store.get_command_history() == []
        assert store.get_recent_files() == []

    def test_corrupt_file_gives_empty_state(self, tmp_path):
        p = tmp_path / "shada"
        p.write_bytes(b"\x00\x01\x02corrupt bytes here")
        store = ShadaStore(path=p)
        store.read()  # should not raise
        assert store.get_command_history() == []

    def test_global_marks_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.set_global_mark("A", Path("/some/file.py"), 10, 3)
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        result = s2.get_global_mark("A")
        assert result is not None
        assert result[0] == Path("/some/file.py")
        assert result[1] == 10
        assert result[2] == 3

    def test_global_mark_missing_returns_none(self, tmp_shada):
        assert tmp_shada.get_global_mark("Z") is None

    def test_registers_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.set_register(0, "hello world")
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        assert s2.get_register(0) == "hello world"

    def test_command_history_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.push_command_history("wq")
        tmp_shada.push_command_history("set number")
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        hist = s2.get_command_history()
        assert hist[0] == "set number"
        assert hist[1] == "wq"

    def test_search_history_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.push_search_history("foo")
        tmp_shada.push_search_history("bar")
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        hist = s2.get_search_history()
        assert hist[0] == "bar"
        assert hist[1] == "foo"

    def test_jump_list_round_trip(self, tmp_shada, tmp_path):
        entries = [(Path("/a.py"), 1, 0, 0), (Path("/b.py"), 5, 2, 3)]
        tmp_shada.set_jump_list(entries)
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        result = s2.get_jump_list()
        assert result[0] == (Path("/a.py"), 1, 0, 0)
        assert result[1] == (Path("/b.py"), 5, 2, 3)

    def test_recent_files_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.push_recent_file("/home/user/foo.py")
        tmp_shada.push_recent_file("/home/user/bar.py")
        tmp_shada.write()
        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()
        files = s2.get_recent_files()
        assert files[0] == "/home/user/bar.py"
        assert files[1] == "/home/user/foo.py"

    def test_project_trust_round_trip(self, tmp_shada, tmp_path):
        tmp_shada.set_project_trust("/repo/example", True)
        tmp_shada.write()

        s2 = ShadaStore(path=tmp_path / "shada")
        s2.read()

        assert s2.get_project_trust("/repo/example") is True


# ---------------------------------------------------------------------------
# Deduplication / rotation logic
# ---------------------------------------------------------------------------


class TestShadaLogic:
    def test_push_command_history_dedup_consecutive(self, tmp_shada):
        tmp_shada.push_command_history("wq")
        tmp_shada.push_command_history("wq")  # duplicate
        assert tmp_shada.get_command_history() == ["wq"]

    def test_push_command_history_non_consecutive_allowed(self, tmp_shada):
        tmp_shada.push_command_history("wq")
        tmp_shada.push_command_history("set number")
        tmp_shada.push_command_history("wq")  # not consecutive with first wq
        assert tmp_shada.get_command_history()[0] == "wq"
        assert len(tmp_shada.get_command_history()) == 3

    def test_set_register_rotation(self, tmp_shada):
        tmp_shada.set_register(0, "first")
        tmp_shada.set_register(0, "second")
        assert tmp_shada.get_register(0) == "second"
        assert tmp_shada.get_register(1) == "first"

    def test_set_register_rotation_chain(self, tmp_shada):
        for i in range(11):  # push 11 values, 0th should be dropped
            tmp_shada.set_register(0, f"val{i}")
        assert tmp_shada.get_register(0) == "val10"
        assert tmp_shada.get_register(9) == "val1"

    def test_push_recent_file_dedup(self, tmp_shada):
        tmp_shada.push_recent_file("/a.py")
        tmp_shada.push_recent_file("/b.py")
        tmp_shada.push_recent_file("/a.py")  # re-add a.py — it should move to front
        files = tmp_shada.get_recent_files()
        assert files[0] == "/a.py"
        assert files.count("/a.py") == 1

    def test_push_recent_file_trim_to_20(self, tmp_shada):
        for i in range(25):
            tmp_shada.push_recent_file(f"/file{i}.py")
        files = tmp_shada.get_recent_files()
        assert len(files) == 20
        assert files[0] == "/file24.py"

    def test_atomic_write_uses_tmp_file(self, tmp_path):
        store = ShadaStore(path=tmp_path / "shada")
        store.push_command_history("test")
        store.write()
        # tmp file should be gone (replaced)
        assert not (tmp_path / "shada.tmp").exists()
        assert (tmp_path / "shada").exists()


# ---------------------------------------------------------------------------
# _merge_history helper
# ---------------------------------------------------------------------------


class TestMergeHistory:
    def test_in_memory_comes_first(self):
        result = _merge_history(["a", "b"], ["c", "d"], 10)
        assert result == ["a", "b", "c", "d"]

    def test_deduplication(self):
        result = _merge_history(["a", "b"], ["b", "c"], 10)
        assert result == ["a", "b", "c"]
        assert result.count("b") == 1

    def test_cap_applied(self):
        result = _merge_history(["a", "b", "c"], ["d", "e", "f"], 4)
        assert result == ["a", "b", "c", "d"]
        assert len(result) == 4

    def test_empty_disk(self):
        result = _merge_history(["a", "b"], [], 10)
        assert result == ["a", "b"]

    def test_empty_memory(self):
        result = _merge_history([], ["x", "y"], 10)
        assert result == ["x", "y"]

    def test_both_empty(self):
        assert _merge_history([], [], 10) == []


# ---------------------------------------------------------------------------
# ShadaStore.merge_write — multi-instance merge
# ---------------------------------------------------------------------------


class TestMergeWrite:
    def test_merge_write_creates_file(self, tmp_path):
        store = ShadaStore(path=tmp_path / "shada")
        store.push_command_history("wq")
        store.merge_write()
        assert (tmp_path / "shada").exists()

    def test_merge_write_command_history_union(self, tmp_path):
        """Commands from a concurrent session are preserved after merge."""
        shada_path = tmp_path / "shada"

        # Simulate first session: write some history to disk
        disk_session = ShadaStore(path=shada_path)
        disk_session.push_command_history("set number")
        disk_session.push_command_history("wq")
        disk_session.write()

        # Second session: has its own history (not on disk yet)
        session2 = ShadaStore(path=shada_path)
        session2.push_command_history("e foo.py")
        session2.push_command_history("w")
        session2.merge_write()

        # Read result back
        result = ShadaStore(path=shada_path)
        result.read()
        hist = result.get_command_history()
        # session2's commands come first (most recent), then disk-only commands
        assert "w" in hist
        assert "e foo.py" in hist
        assert "set number" in hist
        assert "wq" in hist
        # session2 commands should appear before disk-only commands
        assert hist.index("w") < hist.index("set number")

    def test_merge_write_search_history_union(self, tmp_path):
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.push_search_history("foo")
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.push_search_history("bar")
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        hist = result.get_search_history()
        assert "bar" in hist
        assert "foo" in hist

    def test_merge_write_recent_files_union(self, tmp_path):
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.push_recent_file("/old_file.py")
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.push_recent_file("/new_file.py")
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        files = result.get_recent_files()
        assert "/new_file.py" in files
        assert "/old_file.py" in files
        # in-memory (newer) comes first
        assert files.index("/new_file.py") < files.index("/old_file.py")

    def test_merge_write_global_marks_fills_absent_keys(self, tmp_path):
        """Disk marks for keys not in memory are picked up; in-memory wins for shared keys."""
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.set_global_mark("A", Path("/disk/file.py"), 10, 0)
        disk.set_global_mark("B", Path("/disk/file.py"), 20, 0)
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.set_global_mark("A", Path("/memory/file.py"), 99, 0)  # overrides disk A
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        mark_a = result.get_global_mark("A")
        mark_b = result.get_global_mark("B")
        assert mark_a is not None and mark_a[0] == Path("/memory/file.py")  # in-memory wins
        assert mark_b is not None and mark_b[0] == Path("/disk/file.py")  # disk fills absent

    def test_merge_write_file_positions_fills_absent_paths(self, tmp_path):
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.set_file_pos("/a.py", 5, 0)
        disk.set_file_pos("/b.py", 10, 2)
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.set_file_pos("/b.py", 99, 0)  # in-memory overrides /b.py
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        assert result.get_file_pos("/a.py") == (5, 0)  # picked up from disk
        assert result.get_file_pos("/b.py") == (99, 0)  # in-memory wins

    def test_merge_write_project_trust_union(self, tmp_path):
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.set_project_trust("/repo/old", True)
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.set_project_trust("/repo/new", True)
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        assert result.get_project_trust("/repo/old") is True
        assert result.get_project_trust("/repo/new") is True

    def test_merge_write_registers_no_merge(self, tmp_path):
        """Registers are session-local: disk registers should NOT overwrite in-memory."""
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.set_register(0, "disk value")
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.set_register(0, "memory value")
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        assert result.get_register(0) == "memory value"

    def test_merge_write_command_history_no_duplicates(self, tmp_path):
        """Commands present in both in-memory and disk should appear only once."""
        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        disk.push_command_history("shared_cmd")
        disk.write()

        s2 = ShadaStore(path=shada_path)
        s2.push_command_history("shared_cmd")
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        hist = result.get_command_history()
        assert hist.count("shared_cmd") == 1

    def test_merge_write_no_existing_file(self, tmp_path):
        """merge_write works when no shada file exists yet."""
        shada_path = tmp_path / "shada"
        store = ShadaStore(path=shada_path)
        store.push_command_history("first_cmd")
        store.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        assert "first_cmd" in result.get_command_history()

    def test_merge_write_history_cap_respected(self, tmp_path):
        """Total merged command history stays within _CMD_HISTORY_MAX."""
        from peovim.core.shada import _CMD_HISTORY_MAX

        shada_path = tmp_path / "shada"
        disk = ShadaStore(path=shada_path)
        for i in range(80):
            disk._command_history.append(f"disk_{i}")
        disk.write()

        s2 = ShadaStore(path=shada_path)
        for i in range(70):
            s2._command_history.append(f"mem_{i}")
        s2.merge_write()

        result = ShadaStore(path=shada_path)
        result.read()
        assert len(result.get_command_history()) <= _CMD_HISTORY_MAX
