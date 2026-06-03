from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

NEW_YORK_TIME_ZONE = ZoneInfo("America/New_York")


def now_new_york() -> datetime:
    return datetime.now(NEW_YORK_TIME_ZONE)


def iso_now_new_york() -> str:
    return now_new_york().isoformat()


def today_new_york() -> date:
    return now_new_york().date()


def parse_timestamp(value: str | None, *, naive_timezone=UTC) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=naive_timezone)
    return timestamp


def format_new_york_timestamp(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    timestamp = value if isinstance(value, datetime) else parse_timestamp(value)
    if timestamp is None:
        return str(value)
    return timestamp.astimezone(NEW_YORK_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S %Z")
