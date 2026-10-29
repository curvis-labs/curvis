"""Tests for relative timestamps."""

from datetime import UTC, datetime, timedelta

import pytest

from curvis.ui.relative_time import format_relative

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("ago", "text"),
    [
        (timedelta(seconds=-30), "just now"),
        (timedelta(seconds=0), "just now"),
        (timedelta(seconds=59), "just now"),
        (timedelta(minutes=1), "1 minute ago"),
        (timedelta(minutes=59), "59 minutes ago"),
        (timedelta(hours=1), "1 hour ago"),
        (timedelta(hours=23, minutes=59), "23 hours ago"),
        (timedelta(days=1), "yesterday"),
        (timedelta(days=2), "2 days ago"),
        (timedelta(days=6), "6 days ago"),
        (timedelta(days=7), "1 week ago"),
        (timedelta(days=29), "4 weeks ago"),
        (timedelta(days=30), "1 month ago"),
        (timedelta(days=364), "12 months ago"),
        (timedelta(days=365), "1 year ago"),
        (timedelta(days=800), "2 years ago"),
    ],
)
def test_format_relative(ago: timedelta, text: str) -> None:
    assert format_relative(NOW - ago, NOW) == text
