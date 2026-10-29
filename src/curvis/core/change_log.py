"""The change log: an append-only record of every executed, undone and redone command."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ChangeLogEntry:
    """One change: when it happened (UTC), the command type and a short summary."""

    timestamp: datetime
    command_type: str
    summary: str

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() is None:
            raise ValueError("change log timestamps must be timezone-aware (UTC)")


@dataclass
class ChangeLog:
    """Append-only list of :class:`ChangeLogEntry`. Entries are never removed."""

    _entries: list[ChangeLogEntry] = field(default_factory=list)

    def append(self, entry: ChangeLogEntry) -> None:
        """Add an entry at the end."""
        self._entries.append(entry)

    @property
    def entries(self) -> tuple[ChangeLogEntry, ...]:
        """All entries, oldest first."""
        return tuple(self._entries)

    def __iter__(self) -> Iterator[ChangeLogEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int) -> ChangeLogEntry:
        return self._entries[index]
