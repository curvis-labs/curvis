"""Relative timestamps such as "2 days ago" for the recent-projects list."""

from datetime import datetime

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit} ago" if n == 1 else f"{n} {unit}s ago"


def format_relative(then: datetime, now: datetime) -> str:
    """Describe how long before ``now`` the moment ``then`` was, in plain English.

    Both must be timezone-aware. A time in the future (e.g. a clock change) reads
    "just now".
    """
    seconds = int((now - then).total_seconds())
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return _plural(seconds // _MINUTE, "minute")
    if seconds < _DAY:
        return _plural(seconds // _HOUR, "hour")
    days = seconds // _DAY
    if days == 1:
        return "yesterday"
    if days < 7:
        return _plural(days, "day")
    if days < 30:
        return _plural(days // 7, "week")
    if days < 365:
        return _plural(max(1, days // 30), "month")
    return _plural(days // 365, "year")
