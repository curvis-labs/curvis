"""Tab-separated text in the form Excel puts on and reads from the clipboard.

Cells are separated by tabs and rows by line breaks; Excel ends the last row with a
line break too. A cell holding a tab, a line break or a double quote is wrapped in
double quotes, with each quote inside doubled.
"""

_NEEDS_QUOTES = ("\t", "\n", "\r", '"')


def _quote(cell: str) -> str:
    if any(ch in cell for ch in _NEEDS_QUOTES):
        return '"' + cell.replace('"', '""') + '"'
    return cell


def to_tsv(rows: list[list[str]]) -> str:
    """Tab-separated text for a block of cells, each row ending in ``\\r\\n`` like Excel."""
    return "".join("\t".join(_quote(cell) for cell in row) + "\r\n" for row in rows)


def from_tsv(text: str) -> list[list[str]]:
    """A rectangular block of cells from tab-separated text.

    Accepts ``\\r\\n``, ``\\n`` or ``\\r`` line breaks and quoted cells. A final line break
    does not start a new row. Short rows are padded with "" to the widest row.
    """
    rows: list[list[str]] = []
    row: list[str] = []
    cell: list[str] = []
    i, n = 0, len(text)
    at_cell_start = True
    while i < n:
        ch = text[i]
        if at_cell_start and ch == '"':
            # A quoted cell; a quote that does not close it cleanly is taken literally.
            end = i + 1
            parts: list[str] = []
            closed = False
            while end < n:
                if text[end] == '"':
                    if end + 1 < n and text[end + 1] == '"':
                        parts.append('"')
                        end += 2
                        continue
                    closed = True
                    break
                parts.append(text[end])
                end += 1
            if closed and (end + 1 == n or text[end + 1] in "\t\r\n"):
                cell.extend(parts)
                i = end + 1
                at_cell_start = False
                continue
        at_cell_start = False
        if ch == "\t":
            row.append("".join(cell))
            cell = []
            at_cell_start = True
        elif ch in "\r\n":
            row.append("".join(cell))
            rows.append(row)
            row, cell = [], []
            at_cell_start = True
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
        else:
            cell.append(ch)
        i += 1
    if cell or row:
        row.append("".join(cell))
        rows.append(row)
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]
