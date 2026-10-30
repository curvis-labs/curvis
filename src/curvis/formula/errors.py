"""The error raised for a formula that cannot be parsed or evaluated."""


class FormulaError(ValueError):
    """A problem in a formula, with the span of text it refers to.

    ``start`` and ``end`` are 0-based character offsets into the formula text
    (``end`` exclusive), so the UI can underline the problem. The message names the
    1-based character position for people reading it.
    """

    def __init__(self, message: str, start: int, end: int | None = None) -> None:
        self.start = start
        self.end = start + 1 if end is None else max(end, start + 1)
        self.reason = message
        super().__init__(f"{message} (at character {start + 1})")
