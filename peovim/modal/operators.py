"""
modal.operators — Operator handlers: d, y, c, >, <, =, !, g~, gu, gU

Each operator: (document, range, register) -> list[Edit]
Operators consume a motion or text object to determine their range.
"""
