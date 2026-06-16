"""
core.log_manager — LogManager: runtime-configurable logging

Wraps Python's standard logging module to provide:
  - On/off control via enable() / disable()
  - Module pattern filtering ("peovim.core.*", "peovim.ui.event_loop", etc.)
  - Per-module log levels ("peovim.core.*:debug,peovim.ui:info")
  - Always-on in-memory ring buffer for :LogView
  - Optional rotating file handler

Usage in any module:
    import logging
    log = logging.getLogger(__name__)
    log.debug("message")

Singleton access:
    from peovim.core.log_manager import get_log_manager
    mgr = get_log_manager()
    mgr.enable(modules=["peovim.ui.event_loop"], level="debug")
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import pathlib
from collections import deque

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

DEFAULT_LOG_PATH = str(pathlib.Path.home() / ".config" / "peovim" / "peovim.log")
DEFAULT_RING_SIZE = 5000
_FMT = "%(asctime)s.%(msecs)03d  %(levelname)-7s  %(name)-30s  %(message)s"
_DATEFMT = "%H:%M:%S"


# ---------------------------------------------------------------------------
# Ring-buffer handler (always active once LogManager is created)
# ---------------------------------------------------------------------------


class _RingHandler(logging.Handler):
    """Fixed-size in-memory log buffer."""

    def __init__(self, maxlen: int = DEFAULT_RING_SIZE) -> None:
        super().__init__()
        self._buf: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self._buf.append(self.format(record))

    def get_lines(self) -> list[str]:
        return list(self._buf)

    def clear(self) -> None:
        self._buf.clear()


# ---------------------------------------------------------------------------
# Module-pattern filter
# ---------------------------------------------------------------------------


class _ModuleFilter(logging.Filter):
    """
    Allow records only from loggers matching the given module prefixes.

    "peovim.core" matches "peovim.core" and "peovim.core.document" etc.
    Empty patterns list = allow all.
    """

    def __init__(self, patterns: list[str]) -> None:
        super().__init__()
        self._patterns = patterns

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._patterns:
            return True
        return any(record.name == p or record.name.startswith(p + ".") for p in self._patterns)


# ---------------------------------------------------------------------------
# LogManager
# ---------------------------------------------------------------------------


class LogManager:
    """
    Central controller for peovim's logging system.

    The ring buffer is always attached to logging.getLogger("peovim") once
    this manager is created. Only records that pass the current level
    threshold reach it.

    Call enable() to start logging (lowers levels, optionally adds file
    handler). Call disable() to stop.
    """

    def __init__(self) -> None:
        self._ring = _RingHandler(maxlen=DEFAULT_RING_SIZE)
        self._ring.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))

        self._file_handler: logging.handlers.RotatingFileHandler | None = None
        self._active: bool = False
        self._log_path: str = DEFAULT_LOG_PATH

        # Attach ring handler to the "peovim" root logger — always present.
        ed_logger = logging.getLogger("peovim")
        ed_logger.addHandler(self._ring)
        # Default: WARNING so routine INFO/DEBUG doesn't fill the ring buffer.
        ed_logger.setLevel(logging.WARNING)
        # Don't propagate to Python root logger (avoids double-logging).
        ed_logger.propagate = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enable(
        self,
        modules: list[str] | None = None,
        level: str = "DEBUG",
        log_path: str | None = None,
        write_file: bool = True,
    ) -> str:
        """
        Start logging.

        modules: list of patterns like ["peovim.core.*", "peovim.ui.event_loop"].
                 None / empty = all of "peovim".
                 Each entry may carry a per-module level suffix: "peovim.core.*:info"

        level: global fallback level string (DEBUG/INFO/WARNING/ERROR).

        Returns the log file path (or "" if file logging disabled).
        """
        self.disable()  # clean slate

        if log_path:
            self._log_path = log_path

        global_level = self._parse_level(level, logging.DEBUG)

        # Parse module patterns and optional per-module levels.
        # patterns_levels: list of (logger_name, level_int)
        patterns_levels: list[tuple[str, int]] = []
        if modules:
            for entry in modules:
                if ":" in entry:
                    mod_pat, lvl_str = entry.rsplit(":", 1)
                    mod_level = self._parse_level(lvl_str, global_level)
                else:
                    mod_pat = entry
                    mod_level = global_level
                # Strip trailing ".*"
                logger_name = mod_pat.rstrip(".*").rstrip(".")
                if not logger_name:
                    logger_name = "peovim"
                patterns_levels.append((logger_name, mod_level))
        else:
            patterns_levels = [("peovim", global_level)]

        # Set the "peovim" root logger to the lowest requested level so records
        # reach our handlers; per-logger levels do the fine-grained filtering.
        min_level = min(lvl for _, lvl in patterns_levels)
        ed_logger = logging.getLogger("peovim")
        ed_logger.setLevel(min_level)

        # Set per-module logger levels.
        self._active_loggers: list[str] = []
        for logger_name, lvl in patterns_levels:
            logging.getLogger(logger_name).setLevel(lvl)
            self._active_loggers.append(logger_name)

        # Build module filter from the logger name prefixes.
        filter_patterns = [name for name, _ in patterns_levels if name != "peovim"]  # "peovim" = all, no filter needed
        module_filter = _ModuleFilter(filter_patterns)

        # Attach filter to ring handler.
        for f in list(self._ring.filters):
            self._ring.removeFilter(f)
        self._ring.addFilter(module_filter)

        # Optionally add file handler.
        if write_file:
            try:
                pathlib.Path(self._log_path).parent.mkdir(parents=True, exist_ok=True)
                fh = logging.handlers.RotatingFileHandler(
                    self._log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
                )
                fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
                fh.addFilter(module_filter)
                fh.setLevel(min_level)
                ed_logger.addHandler(fh)
                self._file_handler = fh
            except Exception as exc:
                # File logging failed — ring buffer only.
                self._file_handler = None
                logging.getLogger("peovim.core.log_manager").warning(
                    "Could not open log file %s: %s", self._log_path, exc
                )

        self._active = True
        return self._log_path if write_file and self._file_handler else ""

    def disable(self) -> None:
        """Stop logging. Removes handlers and resets logger levels."""
        ed_logger = logging.getLogger("peovim")

        if self._file_handler is not None:
            ed_logger.removeHandler(self._file_handler)
            with contextlib.suppress(Exception):
                self._file_handler.close()
            self._file_handler = None

        # Reset per-module loggers to NOTSET, then set "peovim" root to WARNING.
        for f in list(self._ring.filters):
            self._ring.removeFilter(f)

        for name in getattr(self, "_active_loggers", []):
            if name != "peovim":
                logging.getLogger(name).setLevel(logging.NOTSET)
        self._active_loggers = []

        # "peovim" root always ends up at WARNING (captures errors/warnings even
        # when detailed logging is off, but keeps DEBUG/INFO silent).
        ed_logger.setLevel(logging.WARNING)

        self._active = False

    def set_level(self, level: str, module: str = "peovim") -> None:
        """Adjust the level for a logger without full enable/disable cycle."""
        lvl = self._parse_level(level, logging.DEBUG)
        logging.getLogger(module).setLevel(lvl)
        # Also lower the "peovim" root if needed so records propagate.
        ed_logger = logging.getLogger("peovim")
        if lvl < ed_logger.level:
            ed_logger.setLevel(lvl)

    def get_log_lines(self, last_n: int = 500) -> list[str]:
        """Return up to last_n lines from the in-memory ring buffer."""
        lines = self._ring.get_lines()
        return lines[-last_n:] if last_n < len(lines) else lines

    def clear(self) -> None:
        """Clear the in-memory ring buffer."""
        self._ring.clear()

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def log_path(self) -> str:
        return self._log_path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_level(level_str: str, default: int = logging.DEBUG) -> int:
        return LEVELS.get(level_str.lower().strip(), default)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: LogManager | None = None


def get_log_manager() -> LogManager:
    """Return (or lazily create) the global LogManager singleton."""
    global _manager
    if _manager is None:
        _manager = LogManager()
    return _manager
