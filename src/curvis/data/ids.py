"""Stable identifiers for tables, columns and groups."""

import uuid


def new_id() -> str:
    """Return a new random identifier (32 hex characters)."""
    return uuid.uuid4().hex
