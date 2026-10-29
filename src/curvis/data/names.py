"""Name comparison for tables and columns."""


def name_key(name: str) -> str:
    """Key used to compare names: case-insensitive, so 'Dose' and 'dose' are the same name."""
    return name.casefold()
