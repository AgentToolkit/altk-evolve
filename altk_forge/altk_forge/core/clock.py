"""Timestamps as Forge stores them: UTC, ISO 8601, `Z` suffix."""

from datetime import datetime, timezone

from .errors import InputError


def now():
    return iso(datetime.now(timezone.utc))


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def utc(value, end_of_day=False):
    """A date or date and time as a UTC timestamp; a bare date means the start (or end) of that day."""
    if not value:
        return None
    value = value.strip()
    if "T" not in value:
        value += "T23:59:59.999Z" if end_of_day else "T00:00:00Z"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InputError(f"Invalid date or time {value!r}") from None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
