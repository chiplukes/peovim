from __future__ import annotations


def transform_position(
    line: int,
    col: int,
    *,
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    new_text: str,
) -> tuple[int, int]:
    start = (start_line, start_col)
    end = (end_line, end_col)
    point = (line, col)
    if point < start:
        return point

    parts = new_text.split("\n")
    added_lines = len(parts) - 1
    inserted_tail_col = len(parts[-1])
    inserted_end = (
        (start_line, start_col + inserted_tail_col)
        if added_lines == 0
        else (start_line + added_lines, inserted_tail_col)
    )

    if start == end:
        if line > start_line:
            return (line + added_lines, col)
        if line == start_line:
            if added_lines == 0:
                return (line, col + inserted_tail_col)
            return (inserted_end[0], inserted_end[1] + (col - start_col))
        return point

    if point < end:
        return inserted_end

    deleted_lines = end_line - start_line
    if line > end_line:
        return (line + added_lines - deleted_lines, col)

    tail = col - end_col
    if added_lines == 0:
        return (start_line, start_col + inserted_tail_col + tail)
    return (inserted_end[0], inserted_end[1] + tail)


def transform_range(
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    edit_start_line: int,
    edit_start_col: int,
    edit_end_line: int,
    edit_end_col: int,
    new_text: str,
) -> tuple[tuple[int, int], tuple[int, int]]:
    new_start = transform_position(
        start[0],
        start[1],
        start_line=edit_start_line,
        start_col=edit_start_col,
        end_line=edit_end_line,
        end_col=edit_end_col,
        new_text=new_text,
    )
    new_end = transform_position(
        end[0],
        end[1],
        start_line=edit_start_line,
        start_col=edit_start_col,
        end_line=edit_end_line,
        end_col=edit_end_col,
        new_text=new_text,
    )
    if new_end < new_start:
        return new_start, new_start
    return new_start, new_end
