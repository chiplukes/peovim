"""
lsp.client — LspClient: async JSON-RPC over subprocess stdio.

One LspClient per server process. Runs on a shared asyncio event loop
(in a background thread). Responses are posted back as callables into
a result_queue deque (thread-safe via deque.appendleft).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from peovim.lsp.protocol import FrameBuffer, encode, make_notification, make_request

log = logging.getLogger(__name__)


class LspClient:  # cm:7d5f3a
    """Manages one language server subprocess."""

    def __init__(
        self,
        cmd: list[str],
        root: str,
        loop: asyncio.AbstractEventLoop,
        result_queue: deque,
        notification_handler: Callable[[str, dict], None] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._cmd = cmd
        self._root = root
        self._loop = loop
        self._result_queue = result_queue
        self._notification_handler = notification_handler
        self._env = env

        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._send_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running = False
        self._frame_buf = FrameBuffer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._root,
            env=self._env,
        )
        self._running = True
        asyncio.ensure_future(self._reader())
        asyncio.ensure_future(self._writer())
        asyncio.ensure_future(self._stderr_reader())

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            fut = self.request("shutdown", None)
            await asyncio.wait_for(fut, timeout=3.0)
        except Exception:
            pass
        self.notify("exit", None)
        # Signal writer to stop
        await self._send_queue.put(None)
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                self._proc.kill()

    # ------------------------------------------------------------------
    # Public API (called from asyncio thread)
    # ------------------------------------------------------------------

    def request(self, method: str, params: Any) -> asyncio.Future:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = self._loop.create_future()
        self._pending[req_id] = fut
        msg = make_request(req_id, method, params)
        self._send_queue.put_nowait(encode(msg))
        return fut

    def notify(self, method: str, params: Any) -> None:
        msg = make_notification(method, params)
        self._send_queue.put_nowait(encode(msg))

    # ------------------------------------------------------------------
    # Coroutines
    # ------------------------------------------------------------------

    async def _writer(self) -> None:
        assert self._proc and self._proc.stdin
        try:
            while True:
                data = await self._send_queue.get()
                if data is None:
                    break
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
        except Exception as e:
            log.debug("LSP writer error: %s", e)

    async def _reader(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while self._running:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                messages = self._frame_buf.feed(chunk)
                for msg in messages:
                    self._dispatch(msg)
        except Exception as e:
            log.debug("LSP reader error: %s", e)

    async def _stderr_reader(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            while self._running:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                log.warning("LSP stderr [%s]: %s", self._cmd[0], line.decode("utf-8", errors="replace").rstrip())
        except Exception as e:
            log.debug("LSP stderr reader error: %s", e)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            # Response to a request
            req_id = msg["id"]
            fut = self._pending.pop(req_id, None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
        elif "method" in msg and "id" not in msg:
            # Notification from server
            method = msg["method"]
            params = msg.get("params", {})
            if self._notification_handler:
                try:
                    self._notification_handler(method, params)
                except Exception:
                    log.exception("LSP notification handler error for %s", method)
        elif "method" in msg and "id" in msg:
            # Server-to-client request — send empty response (not implemented)
            log.debug("Unhandled server request: %s", msg["method"])
