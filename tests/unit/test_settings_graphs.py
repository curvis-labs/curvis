"""The Settings of step 3.5: what new graphs start with, the user's palettes, how P
values are shown, export defaults, Clear recent projects and Reset to defaults."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from curvis.analysis.result import PDisplay
from curvis.charts.colors import COLOR_BLIND_SAFE, CURVIS, ColorScheme
from curvis.charts.spec import GraphDefaults
from curvis.core import ProjectConstant
from curvis.settings import Settings, Theme


@pytest.fixture
def ini(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def settings(ini: QSettings) -> Settings:
    return Settings(ini)


MINE = ColorScheme("user:1", "Lab", "#ffffff", "#1a1a1a", ("#AA0000", "#00aa00"))


def test_defaults(settings: Settings) -> None:
    assert settings.graph_defaults() == GraphDefaults()
    assert settings.graph_palette == CURVIS
    assert settings.graph_size == ("wide", None)
    assert settings.graph_font == "dejavu_sans"
    assert settings.graph_text_size == 10.0
    assert settings.palettes == []
    assert settings.p_display is PDisplay.BELOW_0001
    assert (settings.export_format, settings.png_dpi) == ("png", 300)


def test_graph_settings_persist(ini: QSettings) -> None:
    first = Settings(ini)
    first.graph_palette = COLOR_BLIND_SAFE
    first.set_graph_size("custom", (12.0, 8.5))
    first.graph_font = "liberation_sans"
    first.graph_text_size = 8.5
    first.palettes = [MINE]
    first.p_display = PDisplay.EXACT
    first.export_format = "pdf"
    first.png_dpi = 600
    second = Settings(QSettings(ini.fileName(), QSettings.Format.IniFormat))
    assert second.graph_defaults() == GraphDefaults(
        "custom", (12.0, 8.5), COLOR_BLIND_SAFE, "liberation_sans", 8.5
    )
    assert second.palettes == [
        ColorScheme("user:1", "Lab", "#ffffff", "#1a1a1a", ("#aa0000", "#00aa00"))
    ]
    assert second.p_display is PDisplay.EXACT
    assert (second.export_format, second.png_dpi) == ("pdf", 600)


def test_bad_values_are_refused(settings: Settings) -> None:
    with pytest.raises(ValueError):
        settings.graph_font = "comic_sans"
    with pytest.raises(ValueError):
        settings.graph_text_size = 100.0
    with pytest.raises(ValueError):
        settings.set_graph_size("custom", (0.2, 5.0))
    with pytest.raises(ValueError):
        settings.set_graph_size("banner")
    with pytest.raises(ValueError):
        settings.export_format = "gif"
    with pytest.raises(ValueError):
        settings.png_dpi = 20
    with pytest.raises(ValueError):
        settings.graph_palette = ColorScheme("x", "Bad", "#fff", "#000000", ("#123456",))


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        (Settings.GRAPH_PALETTE, "{not json"),
        (Settings.GRAPH_PALETTE, '{"page": "#ffffff", "ink": "#000000", "series": []}'),
        (Settings.GRAPH_SIZE, "custom"),  # no custom size stored
        (Settings.GRAPH_FONT, "wingdings"),
        (Settings.GRAPH_TEXT_SIZE, "huge"),
        (Settings.PALETTES, '[{"series": "#123456"}, 7]'),
        (Settings.P_DISPLAY, "sometimes"),
        (Settings.EXPORT_FORMAT, "bmp"),
        (Settings.PNG_DPI, "9999"),
    ],
)
def test_damaged_values_fall_back_to_defaults(ini: QSettings, key: str, raw: str) -> None:
    ini.setValue(key, raw)
    settings = Settings(ini)
    assert settings.graph_defaults() == GraphDefaults()
    assert settings.palettes == []
    assert settings.p_display is PDisplay.BELOW_0001
    assert (settings.export_format, settings.png_dpi) == ("png", 300)


def test_reset_restores_every_setting_and_keeps_recent_and_constants(
    settings: Settings, tmp_path: Path
) -> None:
    settings.theme = Theme.DARK
    settings.decimal_places = 9
    settings.import_decimal_comma = True
    settings.crash_log = True
    settings.graph_palette = COLOR_BLIND_SAFE
    settings.set_graph_size("square")
    settings.graph_font = "inter"
    settings.graph_text_size = 14.0
    settings.palettes = [MINE]
    settings.p_display = PDisplay.EXACT
    settings.export_format = "svg"
    settings.png_dpi = 150
    settings.add_recent_project(tmp_path / "a.cvp", datetime(2026, 11, 1, tzinfo=UTC))
    settings.add_recent_analysis("t_test")
    constant = ProjectConstant("k", 2.0)
    settings.global_constants = [constant]
    changed: list[str] = []
    settings.changed.connect(changed.append)

    settings.reset_to_defaults()

    assert settings.theme is Theme.SYSTEM and settings.decimal_places == 4
    assert not settings.import_decimal_comma
    assert not settings.crash_log
    assert settings.graph_defaults() == GraphDefaults()
    assert settings.palettes == []
    assert settings.p_display is PDisplay.BELOW_0001
    assert (settings.export_format, settings.png_dpi) == ("png", 300)
    # Kept:
    assert [r.path for r in settings.recent_projects] == [tmp_path / "a.cvp"]
    assert settings.recent_analyses == ["t_test"]
    assert settings.global_constants == [constant]
    assert set(changed) == set(Settings.RESET_KEYS)


def test_clear_recent_projects_keeps_the_files(settings: Settings, tmp_path: Path) -> None:
    project = tmp_path / "a.cvp"
    project.write_bytes(b"data")
    settings.add_recent_project(project)
    settings.clear_recent_projects()
    assert settings.recent_projects == []
    assert project.read_bytes() == b"data"
