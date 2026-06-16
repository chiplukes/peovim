"""
tests for peovim.core.log_manager — LogManager and helpers.
"""

import logging

from peovim.core.log_manager import LEVELS, LogManager, _ModuleFilter, _RingHandler

# ---------------------------------------------------------------------------
# _RingHandler
# ---------------------------------------------------------------------------


class TestRingHandler:
    def _make_record(self, name: str, msg: str, level: int = logging.DEBUG) -> logging.LogRecord:
        return logging.LogRecord(name, level, "", 0, msg, (), None)

    def test_stores_records(self):
        h = _RingHandler(maxlen=10)
        h.setFormatter(logging.Formatter("%(message)s"))
        h.emit(self._make_record("peovim.foo", "hello"))
        h.emit(self._make_record("peovim.foo", "world"))
        lines = h.get_lines()
        assert len(lines) == 2
        assert lines[0] == "hello"
        assert lines[1] == "world"

    def test_maxlen_evicts_oldest(self):
        h = _RingHandler(maxlen=3)
        h.setFormatter(logging.Formatter("%(message)s"))
        for i in range(5):
            h.emit(self._make_record("peovim", str(i)))
        lines = h.get_lines()
        assert len(lines) == 3
        assert lines == ["2", "3", "4"]

    def test_clear(self):
        h = _RingHandler(maxlen=10)
        h.setFormatter(logging.Formatter("%(message)s"))
        h.emit(self._make_record("peovim", "msg"))
        h.clear()
        assert h.get_lines() == []


# ---------------------------------------------------------------------------
# _ModuleFilter
# ---------------------------------------------------------------------------


class TestModuleFilter:
    def _record(self, name: str) -> logging.LogRecord:
        return logging.LogRecord(name, logging.DEBUG, "", 0, "x", (), None)

    def test_empty_patterns_allows_all(self):
        f = _ModuleFilter([])
        assert f.filter(self._record("peovim.core.document"))
        assert f.filter(self._record("anything"))

    def test_exact_match(self):
        f = _ModuleFilter(["peovim.ui.event_loop"])
        assert f.filter(self._record("peovim.ui.event_loop"))
        assert not f.filter(self._record("peovim.ui"))
        assert not f.filter(self._record("peovim.core"))

    def test_prefix_match(self):
        f = _ModuleFilter(["peovim.core"])
        assert f.filter(self._record("peovim.core"))
        assert f.filter(self._record("peovim.core.document"))
        assert f.filter(self._record("peovim.core.buffer"))
        assert not f.filter(self._record("peovim.ui.event_loop"))
        assert not f.filter(self._record("peovim"))

    def test_multiple_patterns(self):
        f = _ModuleFilter(["peovim.core", "peovim.ui.event_loop"])
        assert f.filter(self._record("peovim.core.document"))
        assert f.filter(self._record("peovim.ui.event_loop"))
        assert not f.filter(self._record("peovim.ui.layout"))
        assert not f.filter(self._record("peovim.modal"))


# ---------------------------------------------------------------------------
# LogManager
# ---------------------------------------------------------------------------


class TestLogManager:
    """Each test creates a fresh LogManager to avoid singleton interference."""

    def setup_method(self):
        self.mgr = LogManager()
        # Remove the ring handler from "peovim" logger after test to avoid leakage
        self._ed_logger = logging.getLogger("peovim")

    def teardown_method(self):
        self.mgr.disable()
        # Clean up handler attached during __init__
        self._ed_logger.removeHandler(self.mgr._ring)
        self._ed_logger.setLevel(logging.NOTSET)

    def test_initial_state_inactive(self):
        assert not self.mgr.is_active

    def test_enable_sets_active(self):
        self.mgr.enable(write_file=False)
        assert self.mgr.is_active

    def test_disable_clears_active(self):
        self.mgr.enable(write_file=False)
        self.mgr.disable()
        assert not self.mgr.is_active

    def test_ring_captures_after_enable(self):
        self.mgr.enable(write_file=False, level="debug")
        logger = logging.getLogger("peovim.test_log_capture")
        logger.debug("test-message-xyz")
        lines = self.mgr.get_log_lines()
        assert any("test-message-xyz" in line for line in lines)

    def test_ring_empty_before_enable(self):
        # Fresh manager with WARNING level — DEBUG records shouldn't arrive
        logger = logging.getLogger("peovim.test_silent")
        logger.debug("should-not-appear")
        lines = self.mgr.get_log_lines()
        assert not any("should-not-appear" in line for line in lines)

    def test_module_filter_restricts_output(self):
        self.mgr.enable(modules=["peovim.core"], write_file=False, level="debug")
        logging.getLogger("peovim.core.document").debug("core-msg")
        logging.getLogger("peovim.ui.event_loop").debug("ui-msg")
        lines = self.mgr.get_log_lines()
        assert any("core-msg" in line for line in lines)
        assert not any("ui-msg" in line for line in lines)

    def test_per_module_level_syntax(self):
        # "peovim.core:debug,peovim.ui:warning" — core gets DEBUG, ui gets WARNING
        self.mgr.enable(
            modules=["peovim.core:debug", "peovim.ui:warning"],
            write_file=False,
        )
        assert logging.getLogger("peovim.core").level == logging.DEBUG
        assert logging.getLogger("peovim.ui").level == logging.WARNING

    def test_enable_resets_on_re_enable(self):
        self.mgr.enable(write_file=False)
        self.mgr.enable(modules=["peovim.core"], write_file=False)
        # After second enable, "peovim" logger's level should be updated
        assert self._ed_logger.level <= logging.DEBUG

    def test_disable_resets_ed_logger_to_warning(self):
        self.mgr.enable(write_file=False, level="debug")
        self.mgr.disable()
        assert self._ed_logger.level == logging.WARNING

    def test_get_log_lines_last_n(self):
        self.mgr.enable(write_file=False, level="debug")
        logger = logging.getLogger("peovim.test_last_n")
        for i in range(20):
            logger.debug("line-%d", i)
        lines = self.mgr.get_log_lines(last_n=5)
        assert len(lines) == 5
        assert "line-19" in lines[-1]

    def test_clear(self):
        self.mgr.enable(write_file=False, level="debug")
        logging.getLogger("peovim.test_clear").debug("to-be-cleared")
        self.mgr.clear()
        assert self.mgr.get_log_lines() == []

    def test_set_level(self):
        self.mgr.enable(write_file=False, level="warning")
        self.mgr.set_level("debug", module="peovim.core")
        assert logging.getLogger("peovim.core").level == logging.DEBUG

    def test_parse_log_args_all_defaults(self):
        from peovim.commands.builtin import _parse_log_args

        modules, level, write_file = _parse_log_args("")
        assert modules is None
        assert level == "debug"
        assert write_file is True

    def test_parse_log_args_full(self):
        from peovim.commands.builtin import _parse_log_args

        modules, level, write_file = _parse_log_args("modules=peovim.core.*,peovim.ui level=info file=no")
        assert modules == ["peovim.core.*", "peovim.ui"]
        assert level == "info"
        assert write_file is False

    def test_levels_map_complete(self):
        for name in ("debug", "info", "warning", "warn", "error", "critical"):
            assert name in LEVELS
