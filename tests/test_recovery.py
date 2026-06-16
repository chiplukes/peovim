"""Tests for peovim.core.recovery — RecoveryStore."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from peovim.core.recovery import RecoveryStore, _sanitize_path

# ---------------------------------------------------------------------------
# _sanitize_path
# ---------------------------------------------------------------------------


def test_sanitize_path_unix():
    p = Path("/home/user/project/foo.py")
    s = _sanitize_path(p)
    assert "/" not in s
    assert "foo.py" in s or "foo_py" in s


def test_sanitize_path_windows_style():
    p = Path("C:/Users/chip/project/bar.txt")
    s = _sanitize_path(p)
    assert ":" not in s
    assert "\\" not in s


def test_sanitize_path_max_length():
    long_name = "a" * 300
    p = Path(f"/tmp/{long_name}.py")
    s = _sanitize_path(p)
    assert len(s) <= 180


def test_sanitize_path_stable():
    p = Path("/foo/bar.py")
    assert _sanitize_path(p) == _sanitize_path(p)


# ---------------------------------------------------------------------------
# RecoveryStore — basic I/O
# ---------------------------------------------------------------------------


def test_write_and_read(tmp_path):
    store = RecoveryStore("test-uuid", tmp_path)
    path = Path("/tmp/myfile.py")
    store.write(path, "hello\nworld\n")
    assert store.exists_for_path(path)
    assert store.read(path) == "hello\nworld\n"


def test_delete(tmp_path):
    store = RecoveryStore("test-uuid", tmp_path)
    path = Path("/tmp/myfile.py")
    store.write(path, "content")
    store.delete(path)
    assert not store.exists_for_path(path)


def test_delete_missing_is_noop(tmp_path):
    store = RecoveryStore("test-uuid", tmp_path)
    path = Path("/tmp/nonexistent.py")
    store.delete(path)  # must not raise


def test_exists_for_path_false_when_missing(tmp_path):
    store = RecoveryStore("test-uuid", tmp_path)
    assert not store.exists_for_path(Path("/tmp/missing.py"))


# ---------------------------------------------------------------------------
# RecoveryStore — lockfile
# ---------------------------------------------------------------------------


def test_write_lockfile_creates_file(tmp_path):
    store = RecoveryStore("my-uuid", tmp_path)
    store.write_lockfile()
    assert (tmp_path / "my-uuid.lock").exists()


def test_delete_lockfile_removes_file(tmp_path):
    store = RecoveryStore("my-uuid", tmp_path)
    store.write_lockfile()
    store.delete_lockfile()
    assert not (tmp_path / "my-uuid.lock").exists()


def test_delete_lockfile_missing_is_noop(tmp_path):
    store = RecoveryStore("my-uuid", tmp_path)
    store.delete_lockfile()  # must not raise


# ---------------------------------------------------------------------------
# RecoveryStore — list_orphans
# ---------------------------------------------------------------------------


def test_list_orphans_empty_dir(tmp_path):
    store = RecoveryStore("current-uuid", tmp_path)
    assert store.list_orphans() == []


def test_list_orphans_no_recovery_files(tmp_path):
    store = RecoveryStore("current-uuid", tmp_path)
    store.write_lockfile()
    assert store.list_orphans() == []


def test_list_orphans_finds_crashed_session(tmp_path):
    """A .txt without a matching .lock is an orphan."""
    crashed_uuid = "crashed-uuid"
    # Write a recovery file for the crashed session (no lockfile)
    rec_file = tmp_path / f"{crashed_uuid}_tmp_foo_py.txt"
    rec_file.write_text("recovered content", encoding="utf-8")

    current = RecoveryStore("current-uuid", tmp_path)
    orphans = current.list_orphans()
    assert len(orphans) == 1
    path_fragment, file_path = orphans[0]
    assert path_fragment == "tmp_foo_py"
    assert file_path == rec_file


def test_list_orphans_skips_live_session(tmp_path):
    """A .txt whose UUID has a matching .lock belongs to a live session."""
    live_uuid = "live-uuid"
    rec_file = tmp_path / f"{live_uuid}_tmp_bar_py.txt"
    rec_file.write_text("content", encoding="utf-8")
    lock_file = tmp_path / f"{live_uuid}.lock"
    lock_file.write_text(live_uuid, encoding="utf-8")

    current = RecoveryStore("current-uuid", tmp_path)
    assert current.list_orphans() == []


def test_list_orphans_skips_own_session(tmp_path):
    """Files from the current session UUID are never reported as orphans."""
    store = RecoveryStore("own-uuid", tmp_path)
    store.write_lockfile()
    store.write(Path("/tmp/owned.py"), "mine")
    assert store.list_orphans() == []


def test_list_orphans_mixed(tmp_path):
    """Only orphans (no lock) from other sessions are returned."""
    orphan_uuid = "orphan-uuid"
    live_uuid = "live-uuid"
    own_uuid = "own-uuid"

    (tmp_path / f"{orphan_uuid}_file1_py.txt").write_text("a", encoding="utf-8")
    (tmp_path / f"{live_uuid}_file2_py.txt").write_text("b", encoding="utf-8")
    (tmp_path / f"{live_uuid}.lock").write_text(live_uuid, encoding="utf-8")

    store = RecoveryStore(own_uuid, tmp_path)
    store.write_lockfile()
    store.write(Path("/tmp/own.py"), "own")

    orphans = store.list_orphans()
    assert len(orphans) == 1
    assert orphans[0][0] == "file1_py"


# ---------------------------------------------------------------------------
# RecoveryStore — cleanup_session
# ---------------------------------------------------------------------------


def test_cleanup_session_removes_own_files(tmp_path):
    store = RecoveryStore("sess-uuid", tmp_path)
    paths = [Path("/tmp/a.py"), Path("/tmp/b.py")]
    for p in paths:
        store.write(p, "content")
    store.cleanup_session(paths)
    for p in paths:
        assert not store.exists_for_path(p)


def test_cleanup_session_skips_missing(tmp_path):
    store = RecoveryStore("sess-uuid", tmp_path)
    store.cleanup_session([Path("/tmp/not_written.py")])  # must not raise


# ---------------------------------------------------------------------------
# RecoveryStore — for_this_session factory
# ---------------------------------------------------------------------------


def test_for_this_session_returns_store():
    store = RecoveryStore.for_this_session()
    assert isinstance(store, RecoveryStore)
    assert len(store._uuid) > 0


def test_for_this_session_unique_uuids():
    a = RecoveryStore.for_this_session()
    b = RecoveryStore.for_this_session()
    assert a._uuid != b._uuid


# ---------------------------------------------------------------------------
# Autosave tick (runtime_controller integration)
# ---------------------------------------------------------------------------


def test_autosave_tick_writes_dirty_docs(tmp_path):
    """run_autosave writes recovery files for dirty documents with a path."""

    from peovim.ui.runtime_controller import EventLoopRuntimeController

    store = RecoveryStore("tick-uuid", tmp_path)

    doc = SimpleNamespace(
        dirty=True,
        path=Path("/tmp/test_autosave.py"),
        get_text=lambda: "dirty content\n",
    )
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 1)  # 1 second interval

    host = SimpleNamespace(
        _recovery_store=store,
        _options=options,
        _workspace=workspace,
    )
    ctrl = EventLoopRuntimeController.__new__(EventLoopRuntimeController)
    ctrl._host = host
    ctrl._last_autosave = 0.0

    ctrl.run_autosave(time.monotonic())
    assert store.exists_for_path(doc.path)
    assert store.read(doc.path) == "dirty content\n"


def test_autosave_tick_skips_clean_docs(tmp_path):
    store = RecoveryStore("tick-uuid", tmp_path)

    doc = SimpleNamespace(
        dirty=False,
        path=Path("/tmp/clean.py"),
        get_text=lambda: "clean",
    )
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 1)

    host = SimpleNamespace(
        _recovery_store=store,
        _options=options,
        _workspace=workspace,
    )
    ctrl = object.__new__(
        __import__("peovim.ui.runtime_controller", fromlist=["EventLoopRuntimeController"]).EventLoopRuntimeController
    )
    ctrl._host = host
    ctrl._last_autosave = 0.0

    ctrl.run_autosave(time.monotonic())
    assert not store.exists_for_path(doc.path)


def test_autosave_tick_skips_pathless_docs(tmp_path):
    store = RecoveryStore("tick-uuid", tmp_path)

    doc = SimpleNamespace(dirty=True, path=None, get_text=lambda: "scratch")
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 1)

    host = SimpleNamespace(
        _recovery_store=store,
        _options=options,
        _workspace=workspace,
    )
    from peovim.ui.runtime_controller import EventLoopRuntimeController

    ctrl = EventLoopRuntimeController.__new__(EventLoopRuntimeController)
    ctrl._host = host
    ctrl._last_autosave = 0.0

    ctrl.run_autosave(time.monotonic())
    # No recovery file should be created
    assert list(tmp_path.glob("*.txt")) == []


def test_autosave_interval_respected(tmp_path):
    """A second call before the interval elapses must NOT write again."""
    writes: list[str] = []

    class _FakeStore:
        def write(self, path, text):
            writes.append(text)

        def exists_for_path(self, path):
            return False

    doc = SimpleNamespace(dirty=True, path=Path("/tmp/f.py"), get_text=lambda: "v1")
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 60)  # 60s interval

    host = SimpleNamespace(
        _recovery_store=_FakeStore(),
        _options=options,
        _workspace=workspace,
    )
    from peovim.ui.runtime_controller import EventLoopRuntimeController

    ctrl = EventLoopRuntimeController.__new__(EventLoopRuntimeController)
    ctrl._host = host
    now = time.monotonic()
    ctrl._last_autosave = now  # pretend we just saved

    ctrl.run_autosave(now + 1)  # only 1 second later — should NOT write
    assert writes == []


def test_autosave_disabled_when_interval_zero(tmp_path):
    store = RecoveryStore("tick-uuid", tmp_path)
    doc = SimpleNamespace(dirty=True, path=Path("/tmp/f.py"), get_text=lambda: "v")
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 0)  # 0 = disabled

    host = SimpleNamespace(
        _recovery_store=store,
        _options=options,
        _workspace=workspace,
    )
    from peovim.ui.runtime_controller import EventLoopRuntimeController

    ctrl = EventLoopRuntimeController.__new__(EventLoopRuntimeController)
    ctrl._host = host
    ctrl._last_autosave = 0.0

    ctrl.run_autosave(time.monotonic())
    assert not store.exists_for_path(doc.path)


def test_autosave_no_store(tmp_path):
    """run_autosave with no recovery_store is a no-op."""
    doc = SimpleNamespace(dirty=True, path=Path("/tmp/f.py"), get_text=lambda: "v")
    workspace = SimpleNamespace(documents=[doc])
    options = SimpleNamespace(get=lambda name: 30)

    host = SimpleNamespace(
        _recovery_store=None,
        _options=options,
        _workspace=workspace,
    )
    from peovim.ui.runtime_controller import EventLoopRuntimeController

    ctrl = EventLoopRuntimeController.__new__(EventLoopRuntimeController)
    ctrl._host = host
    ctrl._last_autosave = 0.0
    ctrl.run_autosave(time.monotonic())  # must not raise


# ---------------------------------------------------------------------------
# :RecoverFile command
# ---------------------------------------------------------------------------


def _make_ctx(doc, workspace=None, editor_state=None):
    return SimpleNamespace(
        window=SimpleNamespace(document=doc),
        workspace=workspace,
        editor_state=editor_state,
    )


def test_recoverfile_restores_content(tmp_path):
    from peovim.commands.builtin import _cmd_recoverfile
    from peovim.commands.parser import ParsedCommand
    from peovim.core.document import Document

    store = RecoveryStore("cmd-uuid", tmp_path)
    p = tmp_path / "hello.py"
    p.write_text("original\n", encoding="utf-8")

    doc = Document()
    doc.load(p)
    assert doc.get_text() == "original\n"

    store.write(p, "recovered\ncontent\n")

    es = SimpleNamespace(recovery_store=store, message="")
    workspace = SimpleNamespace(find_document_by_path=lambda path: doc)
    ctx = _make_ctx(doc, workspace=workspace, editor_state=es)

    cmd = ParsedCommand(cmd="RecoverFile", args="")
    _cmd_recoverfile(cmd, ctx)

    assert doc.get_text() == "recovered\ncontent\n"
    assert doc.dirty
    assert not store.exists_for_path(p)
    assert "Recovered" in es.message


def test_recoverfile_no_recovery_file_message(tmp_path):
    from peovim.commands.builtin import _cmd_recoverfile
    from peovim.commands.parser import ParsedCommand
    from peovim.core.document import Document

    store = RecoveryStore("cmd-uuid", tmp_path)
    p = tmp_path / "hello.py"
    p.write_text("original\n", encoding="utf-8")

    doc = Document()
    doc.load(p)

    es = SimpleNamespace(recovery_store=store, message="")
    workspace = SimpleNamespace(find_document_by_path=lambda path: doc)
    ctx = _make_ctx(doc, workspace=workspace, editor_state=es)

    cmd = ParsedCommand(cmd="RecoverFile", args="")
    _cmd_recoverfile(cmd, ctx)

    assert doc.get_text() == "original\n"
    assert "no recovery file" in es.message.lower()


def test_recoverfile_no_store_message(tmp_path):
    from peovim.commands.builtin import _cmd_recoverfile
    from peovim.commands.parser import ParsedCommand
    from peovim.core.document import Document

    p = tmp_path / "hello.py"
    p.write_text("content\n", encoding="utf-8")
    doc = Document()
    doc.load(p)

    es = SimpleNamespace(recovery_store=None, message="")
    ctx = _make_ctx(doc, editor_state=es)

    cmd = ParsedCommand(cmd="RecoverFile", args="")
    _cmd_recoverfile(cmd, ctx)
    assert "no recovery store" in es.message.lower()


def test_recoverfile_with_path_arg(tmp_path):
    from peovim.commands.builtin import _cmd_recoverfile
    from peovim.commands.parser import ParsedCommand
    from peovim.core.document import Document

    store = RecoveryStore("cmd-uuid", tmp_path)
    p = tmp_path / "target.py"
    p.write_text("old\n", encoding="utf-8")

    doc = Document()
    doc.load(p)
    store.write(p, "new\n")

    es = SimpleNamespace(recovery_store=store, message="")
    workspace = SimpleNamespace(find_document_by_path=lambda path: doc)
    # Current window has a different doc (no path)
    other_doc = Document()
    ctx = _make_ctx(other_doc, workspace=workspace, editor_state=es)

    cmd = ParsedCommand(cmd="RecoverFile", args=str(p))
    _cmd_recoverfile(cmd, ctx)
    assert doc.get_text() == "new\n"
