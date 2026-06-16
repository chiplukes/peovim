"""Runtime, maintenance, and render-warning helpers extracted from `EventLoop`."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from peovim.ui.render_jobs import render_runtime_diagnostics

if TYPE_CHECKING:
    from peovim.ui.event_loop import EventLoop

log = logging.getLogger(__name__)


class EventLoopRuntimeController:  # cm:b1c5e8
    """Owns runtime error reporting, maintenance ticks, and dirty-render flushing for `EventLoop`."""

    def __init__(self, host: EventLoop) -> None:
        self._host = host
        self._last_autosave: float = 0.0
        self._last_file_check: float = 0.0
        self._file_check_warned: set[int] = set()  # doc IDs warned for dirty+external-changed
        self._sidebar_blink_phase: int = -1

    def handle_asyncio_exception(self, loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        message = str(context.get("message") or "Unhandled asyncio exception")
        if isinstance(exc, BaseException):
            self.report_runtime_error("asyncio task", exc, detail=message)
            return
        self.report_runtime_error("asyncio task", RuntimeError(message), detail=message)

    def report_runtime_error(self, where: str, exc: BaseException, *, detail: str | None = None) -> None:
        host = self._host
        summary = str(exc).splitlines()[0].strip() or exc.__class__.__name__
        user_message = f"Error in {where}: {summary} (see :messages / :LogView)"
        fingerprint = f"{where}:{exc.__class__.__name__}:{summary}"
        now = time.monotonic()
        last_seen = host._runtime_error_last_seen.get(fingerprint, 0.0)
        host._runtime_error_last_seen[fingerprint] = now

        log_message = where if detail is None else f"{where}: {detail}"
        log.error(log_message, exc_info=(type(exc), exc, exc.__traceback__))

        if host._editor_state is not None and now - last_seen > 2.0:
            host._editor_state.message = user_message
        if host._notify_manager is not None and hasattr(host._notify_manager, "notify") and now - last_seen > 2.0:
            try:
                host._notify_manager.notify(user_message, level="error", title="Error", timeout=8.0)
            except Exception:
                log.exception("notify runtime error failed")
        host._invalidate("full")

    def run_render_maintenance(self, now: float) -> None:
        self.drain_lsp_queue()
        self.drain_picker_async()
        self.update_key_echo_timeout(now)
        self.expire_yank_flash(now)
        self.run_autosave(now)
        self.check_external_changes(now)
        self._tick_sidebar_blink(now)

    def _tick_sidebar_blink(self, now: float) -> None:
        host = self._host
        sidebar = getattr(host, "_sidebar", None)
        if sidebar is None or not getattr(sidebar, "focused", False):
            self._sidebar_blink_phase = -1
            # Reset blink_on to True so selection is visible when not blinking.
            if sidebar is not None and not getattr(sidebar, "blink_on", True):
                sidebar.blink_on = True
                host._invalidate("full")
            return
        blink_phase = int(now * 2) % 2
        if blink_phase != self._sidebar_blink_phase:
            self._sidebar_blink_phase = blink_phase
            sidebar.blink_on = blink_phase == 0
            host._invalidate("full")

    def check_external_changes(self, now: float) -> None:
        _CHECK_INTERVAL = 2.0
        if now - self._last_file_check < _CHECK_INTERVAL:
            return
        self._last_file_check = now
        self._do_check_external_changes()

    def force_check_external_changes(self) -> None:
        """Trigger an immediate external-change check (called by :checktime)."""
        self._last_file_check = 0.0
        self._do_check_external_changes()

    def _do_check_external_changes(self) -> None:
        host = self._host
        if host._editor_state is None:
            return
        active_doc = host._workspace.active_window.document
        for doc in host._workspace.documents:
            if not doc.has_external_changes():
                self._file_check_warned.discard(id(doc))
                continue
            if doc.dirty:
                if id(doc) not in self._file_check_warned:
                    self._file_check_warned.add(id(doc))
                    name = doc.path.name if doc.path else "[No Name]"
                    if doc is active_doc:
                        host._editor_state.message = f'W12: Warning: File "{name}" changed on disk (use :e! to reload)'
                    host._invalidate("full")
            else:
                if doc.path is not None and not doc.path.exists():
                    # File was moved or deleted — warn once and stop retrying.
                    if id(doc) not in self._file_check_warned:
                        self._file_check_warned.add(id(doc))
                        name = doc.path.name
                        log.debug("external-change check: %s no longer exists", doc.path)
                        if doc is active_doc:
                            host._editor_state.message = f'"{name}" was moved or deleted'
                    continue
                self._file_check_warned.discard(id(doc))
                try:
                    doc.reload()
                except Exception as exc:
                    log.warning("auto-reload failed for %s: %s", doc.path, exc)
                    continue
                # Invalidate syntax caches so next render re-parses the new content.
                # remove_buffer() clears _pending_version so any in-flight parse
                # job for the old content is discarded rather than overwriting the
                # cache with stale spans from before the reload.
                buf_id = id(doc)
                host._syntax_submitted.pop(buf_id, None)
                host._syntax_cache.pop(buf_id, None)
                host._syntax_engine.remove_buffer(buf_id)
                if doc is active_doc:
                    name = doc.path.name if doc.path else "[No Name]"
                    host._editor_state.message = f'"{name}" changed on disk — reloaded'
                host._invalidate("full")

    def run_autosave(self, now: float) -> None:
        host = self._host
        recovery_store = getattr(host, "_recovery_store", None)
        if recovery_store is None:
            return
        options = getattr(host, "_options", None)
        interval: int = options.get("autosave_interval") if options is not None else 30
        if interval <= 0:
            return
        if now - self._last_autosave < interval:
            return
        self._last_autosave = now
        for doc in host._workspace.documents:
            if doc.dirty and doc.path is not None:
                try:
                    recovery_store.write(doc.path, doc.get_text())
                except Exception as exc:
                    log.exception("autosave failed for %s: %s", doc.path, exc)

    def update_key_echo_timeout(self, now: float) -> None:
        host = self._host
        if not host._key_echo_active:
            return
        if now - host._key_echo_idle_since >= host._key_echo_idle_secs:
            host._key_echo_active = False
            host._key_echo_keys.clear()
            if host._editor_state is not None:
                host._editor_state.message = ""
            host._invalidate_message()
            return
        host._invalidate_message()

    def expire_yank_flash(self, now: float) -> None:
        host = self._host
        if host._yank_flash_until <= 0 or now < host._yank_flash_until:
            return
        host._yank_flash_until = 0.0
        if host._editor_state is not None:
            win = host._workspace.active_tab.active_window
            host._editor_state.decorations.clear_namespace(id(win.document), "yank:flash")
        host._invalidate("full")

    def render_if_dirty(self) -> None:
        host = self._host
        if not host._dirty:
            return
        reasons = host._consume_invalidation_reasons() or {"full"}
        host._dirty = False
        try:
            host._render(reasons)
        except Exception as exc:
            self.report_runtime_error("render", exc)
        if host._backend.has_pending_output():
            try:
                host._backend.flush()
            except Exception as exc:
                self.report_runtime_error("backend flush", exc)

    def drain_lsp_queue(self) -> None:
        host = self._host
        if not host._lsp_queue:
            return
        changed = False
        while host._lsp_queue:
            try:
                cb = host._lsp_queue.pop()
                cb(host._editor_state, host._workspace)
                changed = True
            except Exception as exc:
                self.report_runtime_error("LSP callback", exc)
        if changed:
            host._invalidate("full")

    def drain_picker_async(self) -> None:
        """Apply pending async picker results (debounced grep, etc.)."""
        host = self._host
        picker = host._picker
        if picker is None:
            return
        try:
            if picker.poll_async_result():
                host._invalidate("full")
        except Exception as exc:
            self.report_runtime_error("picker async", exc)

    def maybe_warn_parallel_render_unavailable(self) -> None:
        host = self._host
        if host._editor_state is None:
            return
        policy = host._resolve_render_execution_policy()
        diagnostics = render_runtime_diagnostics(policy)
        should_warn = diagnostics.requested and not diagnostics.runtime_supported
        if not should_warn:
            host._render_warning_active = False
            return
        if host._render_warning_active:
            return
        host._render_warning_active = True
        host._editor_state.message = f"parallelrender=on unavailable: {diagnostics.reason}; see :checkhealth"
        host._invalidate_message()

    def on_option_changed(self, name: str = "", scope: str = "global", **_kwargs: object) -> None:
        if scope != "global" or name not in {"parallelrender", "parallelrenderworkers"}:
            return
        self.maybe_warn_parallel_render_unavailable()
