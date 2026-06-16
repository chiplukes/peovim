"""
core.decorations_store — DecorationsStore: namespace-isolated decoration storage

Plugins add decorations (highlights, signs, virtual text) to buffers under a
namespace. clear_namespace() removes all decorations for that namespace atomically.
buf_id = id(document) for Phase 6.
"""

from __future__ import annotations

from typing import Any


class DecorationsStore:
    """Namespace-isolated decoration storage for multiple buffers."""

    def __init__(self) -> None:
        # (buf_id, ns) -> list of (dec_id, decoration)
        self._store: dict[tuple[int, str], list[tuple[int, Any]]] = {}
        self._next_id: int = 0

    def add(self, buf_id: int, ns: str, decoration: Any) -> int:
        """Append a decoration under (buf_id, ns). Returns unique dec_id."""
        dec_id = self._next_id
        self._next_id += 1
        key = (buf_id, ns)
        if key not in self._store:
            self._store[key] = []
        self._store[key].append((dec_id, decoration))
        return dec_id

    def remove(self, buf_id: int, ns: str, dec_id: int) -> None:
        """Remove a specific decoration by id."""
        key = (buf_id, ns)
        if key not in self._store:
            return
        self._store[key] = [(i, d) for i, d in self._store[key] if i != dec_id]

    def clear_namespace(self, buf_id: int, ns: str) -> None:
        """Remove all decorations for (buf_id, ns) atomically."""
        self._store.pop((buf_id, ns), None)

    def clear_buffer(self, buf_id: int) -> None:
        """Remove ALL decorations for a buffer (all namespaces)."""
        keys = [k for k in self._store if k[0] == buf_id]
        for k in keys:
            del self._store[k]

    def get_for_buffer(self, buf_id: int) -> list[Any]:
        """Return flat list of all decoration objects for this buffer.

        Sorted: Signs first (by priority desc), then HighlightRegions, then others.
        Uses the `kind` attribute on decorations rather than importing UI classes,
        keeping core/ free of ui/ imports.
        """
        all_decs: list[Any] = []
        for (bid, _ns), entries in self._store.items():
            if bid == buf_id:
                for _did, dec in entries:
                    all_decs.append(dec)

        signs = [d for d in all_decs if getattr(d, "kind", "") == "sign"]
        highlights = [d for d in all_decs if getattr(d, "kind", "") == "highlight"]
        others = [d for d in all_decs if getattr(d, "kind", "") not in ("sign", "highlight")]

        signs.sort(key=lambda s: s.priority, reverse=True)

        return signs + highlights + others

    def get_for_namespace(self, buf_id: int, ns: str) -> list[Any]:
        """Return all decoration objects for (buf_id, ns)."""
        key = (buf_id, ns)
        return [dec for _did, dec in self._store.get(key, [])]

    def list_namespaces(self, buf_id: int) -> list[str]:
        """Return sorted list of namespace names for this buffer."""
        return sorted({ns for (bid, ns) in self._store if bid == buf_id})

    def has_signs(self, buf_id: int) -> bool:
        """Return True if any Sign decoration exists for this buffer."""
        for (bid, _ns), entries in self._store.items():
            if bid == buf_id:
                for _did, dec in entries:
                    if getattr(dec, "kind", "") == "sign":
                        return True
        return False
