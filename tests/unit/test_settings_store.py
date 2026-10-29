"""Tests for the settings store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from curvis.settings import MAX_RECENT, Settings, Theme


@pytest.fixture
def ini(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def settings(ini: QSettings) -> Settings:
    return Settings(ini)


T0 = datetime(2026, 10, 1, tzinfo=UTC)


def test_defaults(settings: Settings) -> None:
    assert settings.theme is Theme.SYSTEM
    assert settings.decimal_places == 4
    assert settings.recent_projects == []


def test_values_persist(ini: QSettings, tmp_path: Path) -> None:
    first = Settings(ini)
    first.theme = Theme.DARK
    first.decimal_places = 7
    first.add_recent_project(tmp_path / "a.cvp", T0)
    second = Settings(QSettings(ini.fileName(), QSettings.Format.IniFormat))
    assert second.theme is Theme.DARK
    assert second.decimal_places == 7
    assert [r.path for r in second.recent_projects] == [tmp_path / "a.cvp"]
    assert second.recent_projects[0].opened_at == T0


def test_changed_signal_sends_key(settings: Settings) -> None:
    seen: list[str] = []
    settings.changed.connect(seen.append)
    settings.theme = Theme.LIGHT
    settings.decimal_places = 3
    assert seen == [Settings.THEME, Settings.DECIMAL_PLACES]


@pytest.mark.parametrize("bad", ["purple", "", "3"])
def test_bad_theme_falls_back_to_system(ini: QSettings, settings: Settings, bad: str) -> None:
    ini.setValue(Settings.THEME, bad)
    assert settings.theme is Theme.SYSTEM


@pytest.mark.parametrize("bad", ["many", "-1", "16", "2.5"])
def test_bad_decimal_places_fall_back(ini: QSettings, settings: Settings, bad: str) -> None:
    ini.setValue(Settings.DECIMAL_PLACES, bad)
    assert settings.decimal_places == 4


@pytest.mark.parametrize("bad", [-1, 16])
def test_setting_decimal_places_out_of_range_raises(settings: Settings, bad: int) -> None:
    with pytest.raises(ValueError, match="between 0 and 15"):
        settings.decimal_places = bad


def test_recent_newest_first_and_moved_to_top(settings: Settings, tmp_path: Path) -> None:
    a, b = tmp_path / "a.cvp", tmp_path / "b.cvp"
    settings.add_recent_project(a, T0)
    settings.add_recent_project(b, T0 + timedelta(hours=1))
    assert [r.path for r in settings.recent_projects] == [b, a]
    settings.add_recent_project(tmp_path / "." / "a.cvp", T0 + timedelta(hours=2))
    assert [r.name for r in settings.recent_projects] == ["a", "b"]


def test_recent_list_is_capped(settings: Settings, tmp_path: Path) -> None:
    for i in range(MAX_RECENT + 5):
        settings.add_recent_project(tmp_path / f"{i}.cvp", T0 + timedelta(minutes=i))
    names = [r.name for r in settings.recent_projects]
    assert len(names) == MAX_RECENT
    assert names[0] == str(MAX_RECENT + 4)


def test_remove_and_clear_recent(settings: Settings, tmp_path: Path) -> None:
    settings.add_recent_project(tmp_path / "a.cvp", T0)
    settings.add_recent_project(tmp_path / "b.cvp", T0)
    settings.remove_recent_project(tmp_path / "a.cvp")
    assert [r.name for r in settings.recent_projects] == ["b"]
    settings.clear_recent_projects()
    assert settings.recent_projects == []


@pytest.mark.parametrize(
    "raw",
    ["not json", "{}", '[{"path": "x.cvp"}]', '[{"path": "x.cvp", "opened_at": "soon"}]', "[1]"],
)
def test_damaged_recent_list_is_skipped(ini: QSettings, settings: Settings, raw: str) -> None:
    ini.setValue(Settings.RECENT, raw)
    assert settings.recent_projects == []


def test_naive_recent_timestamp_read_as_utc(ini: QSettings, settings: Settings) -> None:
    ini.setValue(Settings.RECENT, '[{"path": "x.cvp", "opened_at": "2026-10-01T00:00:00"}]')
    assert settings.recent_projects[0].opened_at == T0
