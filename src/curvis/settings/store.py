"""App settings stored with QSettings: theme, decimal places and recent projects.

Values are validated on read, so a damaged or hand-edited settings file falls back to the
defaults instead of breaking the app. Recent projects are stored as one JSON string, which
behaves the same in the Windows registry, macOS plists and INI files.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

ORGANIZATION = "Curvis"
ORGANIZATION_DOMAIN = "curvis.org"
APPLICATION = "Curvis"

MAX_RECENT = 10
"""Number of recent projects kept."""

DECIMAL_PLACES_DEFAULT = 4
DECIMAL_PLACES_MIN = 0
DECIMAL_PLACES_MAX = 15


class Theme(StrEnum):
    """Colour theme of the app."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        """Name shown in the Settings dialog."""
        return {"system": "Follow the system", "light": "Light", "dark": "Dark"}[self.value]


@dataclass(frozen=True)
class RecentProject:
    """A project file and when it was last opened or saved (UTC)."""

    path: Path
    opened_at: datetime

    @property
    def name(self) -> str:
        """File name without the extension, as shown in the launcher."""
        return self.path.stem


def _same_file_key(path: Path) -> str:
    """Key that treats two spellings of the same file as equal."""
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


class Settings(QObject):
    """Typed access to the app settings. ``changed`` sends the key that changed."""

    changed = Signal(str)

    THEME = "appearance/theme"
    DECIMAL_PLACES = "tables/decimal_places"
    RECENT = "projects/recent"

    def __init__(self, store: QSettings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store if store is not None else QSettings(ORGANIZATION, APPLICATION)

    @property
    def store(self) -> QSettings:
        return self._store

    # --- theme -------------------------------------------------------------------

    @property
    def theme(self) -> Theme:
        value = self._store.value(self.THEME, Theme.SYSTEM.value)
        try:
            return Theme(str(value))
        except ValueError:
            return Theme.SYSTEM

    @theme.setter
    def theme(self, theme: Theme) -> None:
        self._set(self.THEME, Theme(theme).value)

    # --- decimal places ----------------------------------------------------------

    @property
    def decimal_places(self) -> int:
        """Decimal places shown in calculated columns."""
        value = self._store.value(self.DECIMAL_PLACES, DECIMAL_PLACES_DEFAULT)
        try:
            places = int(str(value))
        except ValueError:
            return DECIMAL_PLACES_DEFAULT
        if not DECIMAL_PLACES_MIN <= places <= DECIMAL_PLACES_MAX:
            return DECIMAL_PLACES_DEFAULT
        return places

    @decimal_places.setter
    def decimal_places(self, places: int) -> None:
        if not DECIMAL_PLACES_MIN <= places <= DECIMAL_PLACES_MAX:
            raise ValueError(
                f"Decimal places must be between {DECIMAL_PLACES_MIN} and {DECIMAL_PLACES_MAX}."
            )
        self._set(self.DECIMAL_PLACES, int(places))

    # --- recent projects ---------------------------------------------------------

    @property
    def recent_projects(self) -> list[RecentProject]:
        """Recent projects, newest first. Unreadable entries are skipped."""
        raw = self._store.value(self.RECENT, "[]")
        try:
            items = json.loads(str(raw))
        except ValueError:
            return []
        if not isinstance(items, list):
            return []
        result: list[RecentProject] = []
        for item in items:
            try:
                opened = datetime.fromisoformat(item["opened_at"])
                if opened.utcoffset() is None:
                    opened = opened.replace(tzinfo=UTC)
                result.append(RecentProject(Path(item["path"]), opened))
            except (KeyError, TypeError, ValueError):
                continue
        result.sort(key=lambda r: r.opened_at, reverse=True)
        return result[:MAX_RECENT]

    def add_recent_project(self, path: Path, when: datetime | None = None) -> None:
        """Put ``path`` at the top of the recent list (moving it if it is there already)."""
        when = when if when is not None else datetime.now(UTC)
        key = _same_file_key(path)
        others = [r for r in self.recent_projects if _same_file_key(r.path) != key]
        self._write_recent([RecentProject(path, when), *others])

    def remove_recent_project(self, path: Path) -> None:
        key = _same_file_key(path)
        self._write_recent([r for r in self.recent_projects if _same_file_key(r.path) != key])

    def clear_recent_projects(self) -> None:
        self._write_recent([])

    def _write_recent(self, projects: list[RecentProject]) -> None:
        data = [
            {"path": str(r.path), "opened_at": r.opened_at.astimezone(UTC).isoformat()}
            for r in projects[:MAX_RECENT]
        ]
        self._set(self.RECENT, json.dumps(data))

    # --- helpers -----------------------------------------------------------------

    def _set(self, key: str, value: object) -> None:
        self._store.setValue(key, value)
        self._store.sync()
        self.changed.emit(key)
