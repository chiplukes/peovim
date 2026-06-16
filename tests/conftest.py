"""
Test configuration and shared fixtures.

All tests use HeadlessBackend — no real terminal required.
"""

import pytest

from peovim.core.buffer import PieceTable
from peovim.core.document import Document


@pytest.fixture
def make_buffer():
    """Factory: make_buffer(content="") -> PieceTable"""

    def _make(content: str = "") -> PieceTable:
        t = PieceTable()
        t.load(content.encode())
        return t

    return _make


@pytest.fixture
def make_document():
    """Factory: make_document(content="") -> Document"""

    def _make(content: str = "") -> Document:
        doc = Document()
        doc.load_string(content)
        return doc

    return _make
