"""Backend selection helpers."""

from __future__ import annotations

import logging
import os

from peovim.ui.backend import TerminalBackend

log = logging.getLogger(__name__)

_DEFAULT_BACKEND = "prompt_toolkit"
_BACKEND_ALIASES = {
    "prompt-toolkit": "prompt_toolkit",
    "prompt_toolkit": "prompt_toolkit",
    "pt": "prompt_toolkit",
    "headless": "headless",
    "crossterm": "crossterm",
}


def _create_prompt_toolkit_backend() -> TerminalBackend:
    from peovim.ui.backends.prompt_toolkit import PromptToolkitBackend

    return PromptToolkitBackend()


def _create_headless_backend() -> TerminalBackend:
    from peovim.ui.backends.headless import HeadlessBackend

    return HeadlessBackend()


def _create_crossterm_backend() -> TerminalBackend | None:
    try:
        from peovim.ui.backends.crossterm import CrosstermBackend

        return CrosstermBackend()
    except ImportError:
        return None


def _normalise_backend_name(requested: str | None) -> str:
    candidate = (requested or os.environ.get("ED_BACKEND") or _DEFAULT_BACKEND).strip().lower()
    return _BACKEND_ALIASES.get(candidate, candidate)


def create_backend(requested: str | None = None) -> TerminalBackend:  # cm:5b4a9e
    """Create the requested terminal backend, falling back to prompt_toolkit."""
    backend_name = _normalise_backend_name(requested)

    if backend_name == "headless":
        return _create_headless_backend()

    if backend_name == "crossterm":
        backend = _create_crossterm_backend()
        if backend is not None:
            return backend
        log.warning("Crossterm backend unavailable; falling back to %s", _DEFAULT_BACKEND)
        return _create_prompt_toolkit_backend()

    if backend_name != _DEFAULT_BACKEND:
        log.warning("Unknown backend '%s'; falling back to %s", backend_name, _DEFAULT_BACKEND)

    return _create_prompt_toolkit_backend()
