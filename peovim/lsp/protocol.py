"""
lsp.protocol — Content-Length framing for LSP JSON-RPC messages.
"""

from __future__ import annotations

import json


def encode(msg: dict) -> bytes:  # cm:9f4b2d
    """Encode a JSON-RPC message with Content-Length framing."""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def make_request(id: int, method: str, params: dict | list | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: dict | list | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_response(id: int, result: object) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def path_to_uri(path: str) -> str:
    """Convert a filesystem path to a file:// URI."""
    import pathlib

    p = pathlib.Path(path).resolve()
    return p.as_uri()


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a filesystem path string."""
    import sys
    import urllib.parse

    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        return uri
    path_str = urllib.parse.unquote(parsed.path)
    # On Windows, file URIs have a leading slash before the drive letter: /C:/...
    if sys.platform == "win32" and path_str.startswith("/") and len(path_str) > 2 and path_str[2] == ":":
        path_str = path_str[1:]
    import pathlib

    return str(pathlib.Path(path_str))


class FrameBuffer:
    """Stateful reader that extracts complete LSP frames from a byte stream."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, data: bytes) -> list[dict]:
        """Feed raw bytes; return list of fully parsed message dicts."""
        self._buf += data
        messages = []
        while True:
            msg = self._try_parse()
            if msg is None:
                break
            messages.append(msg)
        return messages

    def _try_parse(self) -> dict | None:
        sep = b"\r\n\r\n"
        idx = self._buf.find(sep)
        if idx == -1:
            return None
        header = self._buf[:idx].decode("ascii", errors="replace")
        content_length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                import contextlib

                with contextlib.suppress(ValueError):
                    content_length = int(line.split(":", 1)[1].strip())
        if content_length is None:
            # Malformed header — skip to next potential frame
            self._buf = self._buf[idx + len(sep) :]
            return None
        body_start = idx + len(sep)
        if len(self._buf) < body_start + content_length:
            return None  # incomplete
        body = self._buf[body_start : body_start + content_length]
        self._buf = self._buf[body_start + content_length :]
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None
