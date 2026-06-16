"""
core.transaction — Cross-buffer atomic undo

editor.transaction() context manager groups edits across multiple Documents
into a single undo step. If the block raises, all changes are rolled back.
Used by LSP rename-symbol, AI rewrite, and any multi-file refactor.

See notes/api.md for the editor transaction API.
"""
