"""App settings stored with QSettings: theme, decimal places, what new graphs start
with (palette, size, font, text size) and the palettes the user made, how P values are
shown, export defaults, the crash log, recent projects, recently used analyses and
global constants.

Settings only give starting values: a new graph copies them into its chart spec
(:meth:`Settings.graph_defaults`), so a later change never alters an existing graph.
:meth:`Settings.reset_to_defaults` restores every setting the Settings dialog shows and
keeps recent projects, recently used analyses and global constants.

Values are validated on read, so a damaged or hand-edited settings file falls back to the
defaults instead of breaking the app. Recent projects and global constants are each stored
as one JSON string, which behaves the same in the Windows registry, macOS plists and INI files.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from curvis.analysis.result import DEFAULT_P_DISPLAY, PDisplay
from curvis.charts.colors import CURVIS, ColorScheme, normalized, palette_problem
from curvis.charts.fonts import DEFAULT_FONT, FONTS
from curvis.charts.spec import (
    ASPECT_PRESETS,
    CUSTOM_ASPECT,
    DEFAULT_ASPECT,
    DEFAULT_TEXT_SIZE,
    MAX_TEXT_SIZE,
    MIN_TEXT_SIZE,
    GraphDefaults,
    palette_from_json,
    palette_to_json,
)
from curvis.core.constants import ProjectConstant

ORGANIZATION = "Curvis"
ORGANIZATION_DOMAIN = "curvis.org"
APPLICATION = "Curvis"

MAX_RECENT = 10
"""Number of recent projects kept."""

MAX_RECENT_ANALYSES = 5
"""Number of recently used analysis types kept."""

DECIMAL_PLACES_DEFAULT = 4
DECIMAL_PLACES_MIN = 0
DECIMAL_PLACES_MAX = 15

GRAPH_SIZE_RANGE = (1.0, 50.0)
"""Smallest and largest custom page side, in cm (as the Format dialog's)."""
EXPORT_FORMATS = ("png", "svg", "pdf")
EXPORT_FORMAT_DEFAULT = "png"
PNG_DPI_DEFAULT = 300
PNG_DPI_MIN, PNG_DPI_MAX = 72, 1200
MAX_PALETTES = 50


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


def _checked(palette: ColorScheme) -> ColorScheme:
    problem = palette_problem(palette)
    if problem is not None:
        raise ValueError(problem)
    return normalized(palette)


def _same_file_key(path: Path) -> str:
    """Key that treats two spellings of the same file as equal."""
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


class Settings(QObject):
    """Typed access to the app settings. ``changed`` sends the key that changed."""

    changed = Signal(str)

    THEME = "appearance/theme"
    DECIMAL_PLACES = "tables/decimal_places"
    IMPORT_DECIMAL_COMMA = "tables/import_decimal_comma"
    RECENT = "projects/recent"
    GLOBAL_CONSTANTS = "formulas/global_constants"
    RECENT_ANALYSES = "analyses/recent"
    CRASH_LOG = "privacy/crash_log"
    GRAPH_PALETTE = "graphs/palette"
    GRAPH_SIZE = "graphs/size"
    GRAPH_CUSTOM_SIZE = "graphs/custom_size"
    GRAPH_FONT = "graphs/font"
    GRAPH_TEXT_SIZE = "graphs/text_size"
    PALETTES = "graphs/palettes"
    P_DISPLAY = "results/p_display"
    EXPORT_FORMAT = "export/format"
    PNG_DPI = "export/png_dpi"

    RESET_KEYS = (
        THEME,
        DECIMAL_PLACES,
        IMPORT_DECIMAL_COMMA,
        CRASH_LOG,
        GRAPH_PALETTE,
        GRAPH_SIZE,
        GRAPH_CUSTOM_SIZE,
        GRAPH_FONT,
        GRAPH_TEXT_SIZE,
        PALETTES,
        P_DISPLAY,
        EXPORT_FORMAT,
        PNG_DPI,
    )
    """What Reset to defaults restores: every setting of the Settings dialog. Recent
    projects, recently used analyses and global constants are kept."""

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

    # --- import decimal mark ----------------------------------------------------

    @property
    def import_decimal_comma(self) -> bool:
        """The default decimal mark for import: a comma (True) or a point (False, the
        default). Auto-detect falls back to it when the data does not settle it."""
        value = self._store.value(self.IMPORT_DECIMAL_COMMA, False)
        return str(value).lower() in ("true", "1")

    @import_decimal_comma.setter
    def import_decimal_comma(self, comma: bool) -> None:
        self._set(self.IMPORT_DECIMAL_COMMA, bool(comma))

    # --- crash log -----------------------------------------------------------------

    @property
    def crash_log(self) -> bool:
        """Keep a local crash log (opt-in, off by default; nothing is sent)."""
        value = self._store.value(self.CRASH_LOG, False)
        return str(value).lower() in ("true", "1")

    @crash_log.setter
    def crash_log(self, enabled: bool) -> None:
        self._set(self.CRASH_LOG, bool(enabled))

    # --- new graphs ------------------------------------------------------------------

    @property
    def graph_palette(self) -> ColorScheme:
        """The palette new graphs copy (default: Curvis)."""
        return self._palette(self._store.value(self.GRAPH_PALETTE, "")) or CURVIS

    @graph_palette.setter
    def graph_palette(self, palette: ColorScheme) -> None:
        self._set(self.GRAPH_PALETTE, json.dumps(palette_to_json(_checked(palette))))

    @property
    def palettes(self) -> list[ColorScheme]:
        """The palettes the user made with the palette editor, in order."""
        raw = self._store.value(self.PALETTES, "[]")
        try:
            items = json.loads(str(raw))
        except ValueError:
            return []
        if not isinstance(items, list):
            return []
        found: list[ColorScheme] = []
        for item in items[:MAX_PALETTES]:
            try:
                found.append(palette_from_json(item))
            except (ValueError, KeyError, TypeError):
                continue
        return found

    @palettes.setter
    def palettes(self, palettes: list[ColorScheme]) -> None:
        data = [palette_to_json(_checked(p)) for p in palettes[:MAX_PALETTES]]
        self._set(self.PALETTES, json.dumps(data))

    @property
    def graph_size(self) -> tuple[str, tuple[float, float] | None]:
        """(page preset, custom size in cm or None) new graphs start with."""
        preset = str(self._store.value(self.GRAPH_SIZE, DEFAULT_ASPECT))
        if preset in ASPECT_PRESETS:
            return preset, None
        if preset == CUSTOM_ASPECT:
            size = self._custom_size()
            if size is not None:
                return CUSTOM_ASPECT, size
        return DEFAULT_ASPECT, None

    def set_graph_size(self, preset: str, custom: tuple[float, float] | None = None) -> None:
        if preset == CUSTOM_ASPECT:
            low, high = GRAPH_SIZE_RANGE
            if custom is None or not all(low <= v <= high for v in custom):
                raise ValueError(f"A custom size is {low:g} to {high:g} cm on each side.")
            self._store.setValue(self.GRAPH_CUSTOM_SIZE, json.dumps(list(custom)))
        elif preset not in ASPECT_PRESETS:
            raise ValueError(f"Unknown graph size {preset!r}.")
        self._set(self.GRAPH_SIZE, preset)

    def _custom_size(self) -> tuple[float, float] | None:
        try:
            raw = json.loads(str(self._store.value(self.GRAPH_CUSTOM_SIZE, "null")))
            width, height = float(raw[0]), float(raw[1])
        except (ValueError, TypeError, IndexError, KeyError):
            return None
        low, high = GRAPH_SIZE_RANGE
        return (width, height) if low <= width <= high and low <= height <= high else None

    @property
    def graph_font(self) -> str:
        """Id of the chart font new graphs use."""
        value = str(self._store.value(self.GRAPH_FONT, DEFAULT_FONT))
        return value if value in FONTS else DEFAULT_FONT

    @graph_font.setter
    def graph_font(self, font_id: str) -> None:
        if font_id not in FONTS:
            raise ValueError(f"Unknown chart font {font_id!r}.")
        self._set(self.GRAPH_FONT, font_id)

    @property
    def graph_text_size(self) -> float:
        """Chart text size (points) of new graphs."""
        try:
            size = float(str(self._store.value(self.GRAPH_TEXT_SIZE, DEFAULT_TEXT_SIZE)))
        except ValueError:
            return DEFAULT_TEXT_SIZE
        return size if MIN_TEXT_SIZE <= size <= MAX_TEXT_SIZE else DEFAULT_TEXT_SIZE

    @graph_text_size.setter
    def graph_text_size(self, size: float) -> None:
        if not MIN_TEXT_SIZE <= size <= MAX_TEXT_SIZE:
            raise ValueError(
                f"Chart text size must be from {MIN_TEXT_SIZE:g} to {MAX_TEXT_SIZE:g} points."
            )
        self._set(self.GRAPH_TEXT_SIZE, float(size))

    def graph_defaults(self) -> GraphDefaults:
        """What a new graph copies into its chart spec."""
        preset, custom = self.graph_size
        return GraphDefaults(
            aspect_ratio=preset,
            custom_size=custom,
            palette=self.graph_palette,
            font=self.graph_font,
            text_size=self.graph_text_size,
        )

    def _palette(self, raw: object) -> ColorScheme | None:
        if not raw:
            return None
        try:
            return palette_from_json(json.loads(str(raw)))
        except (ValueError, KeyError, TypeError):
            return None

    # --- results and export ---------------------------------------------------------

    @property
    def p_display(self) -> PDisplay:
        """How P values are shown (only the display; results keep full precision)."""
        try:
            return PDisplay(str(self._store.value(self.P_DISPLAY, DEFAULT_P_DISPLAY.value)))
        except ValueError:
            return DEFAULT_P_DISPLAY

    @p_display.setter
    def p_display(self, display: PDisplay) -> None:
        self._set(self.P_DISPLAY, PDisplay(display).value)

    @property
    def export_format(self) -> str:
        """The format the Export dialog starts with: "png", "svg" or "pdf"."""
        value = str(self._store.value(self.EXPORT_FORMAT, EXPORT_FORMAT_DEFAULT))
        return value if value in EXPORT_FORMATS else EXPORT_FORMAT_DEFAULT

    @export_format.setter
    def export_format(self, fmt: str) -> None:
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"Unknown export format {fmt!r}.")
        self._set(self.EXPORT_FORMAT, fmt)

    @property
    def png_dpi(self) -> int:
        """The PNG resolution the Export dialog starts with."""
        try:
            dpi = int(str(self._store.value(self.PNG_DPI, PNG_DPI_DEFAULT)))
        except ValueError:
            return PNG_DPI_DEFAULT
        return dpi if PNG_DPI_MIN <= dpi <= PNG_DPI_MAX else PNG_DPI_DEFAULT

    @png_dpi.setter
    def png_dpi(self, dpi: int) -> None:
        if not PNG_DPI_MIN <= dpi <= PNG_DPI_MAX:
            raise ValueError(f"Choose a resolution from {PNG_DPI_MIN} to {PNG_DPI_MAX} dpi.")
        self._set(self.PNG_DPI, int(dpi))

    # --- reset -----------------------------------------------------------------------

    def reset_to_defaults(self) -> None:
        """Restore every setting of the Settings dialog; recent projects, recently used
        analyses and global constants are kept."""
        for key in self.RESET_KEYS:
            self._store.remove(key)
        self._store.sync()
        for key in self.RESET_KEYS:
            self.changed.emit(key)

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

    # --- recently used analyses ---------------------------------------------------

    @property
    def recent_analyses(self) -> list[str]:
        """Ids of the analysis types used most recently, newest first."""
        raw = self._store.value(self.RECENT_ANALYSES, "[]")
        try:
            items = json.loads(str(raw))
        except ValueError:
            return []
        if not isinstance(items, list):
            return []
        ids = [item for item in items if isinstance(item, str) and item]
        return list(dict.fromkeys(ids))[:MAX_RECENT_ANALYSES]

    def add_recent_analysis(self, type_id: str) -> None:
        """Put the analysis type ``type_id`` at the top of the recently used list."""
        others = [t for t in self.recent_analyses if t != type_id]
        self._set(self.RECENT_ANALYSES, json.dumps([type_id, *others][:MAX_RECENT_ANALYSES]))

    # --- global constants ----------------------------------------------------------

    @property
    def global_constants(self) -> list[ProjectConstant]:
        """User constants available in every project (a project constant overrides one).

        Unreadable entries are skipped.
        """
        raw = self._store.value(self.GLOBAL_CONSTANTS, "[]")
        try:
            items = json.loads(str(raw))
        except ValueError:
            return []
        if not isinstance(items, list):
            return []
        result: list[ProjectConstant] = []
        for item in items:
            try:
                value = item["value"]
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                result.append(
                    ProjectConstant(
                        name=str(item["name"]),
                        value=value,
                        unit=str(item.get("unit", "")),
                        id=str(item["id"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return result

    @global_constants.setter
    def global_constants(self, constants: list[ProjectConstant]) -> None:
        data = [{"id": c.id, "name": c.name, "value": c.value, "unit": c.unit} for c in constants]
        self._set(self.GLOBAL_CONSTANTS, json.dumps(data))

    # --- helpers -----------------------------------------------------------------

    def _set(self, key: str, value: object) -> None:
        self._store.setValue(key, value)
        self._store.sync()
        self.changed.emit(key)
