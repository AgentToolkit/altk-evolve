"""Kubernetes-compatible CronJob timing fields, independent of a job template."""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator

UTC = dt.UTC
DESCRIPTORS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def utc(value: dt.datetime) -> dt.datetime:
    """Require an absolute timestamp and normalize it for durable comparisons."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


class CronJobSpec(BaseModel):
    """Timing subset of batch/v1 CronJobSpec; Evolve defaults the timezone to UTC."""

    model_config = ConfigDict(extra="forbid")
    schedule: str
    timeZone: str = "Etc/UTC"
    concurrencyPolicy: Literal["Allow", "Forbid", "Replace"] = "Allow"
    startingDeadlineSeconds: int | None = Field(default=None, ge=0, strict=True)
    suspend: bool = False

    @field_validator("schedule")
    @classmethod
    def valid_cron(cls, value: str) -> str:
        """Reject non-Kubernetes extensions rather than silently changing semantics."""
        expression = DESCRIPTORS.get(value.strip().lower(), value.strip())
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("schedule must contain five cron fields or a supported calendar descriptor")
        for index, field in enumerate(fields):
            names = {
                3: {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"},
                4: {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"},
            }.get(index, set())
            if any(word.upper() not in names for word in re.findall(r"[A-Za-z]+", field)):
                raise ValueError("schedule contains an unsupported cron extension")
            if not re.fullmatch(r"[A-Za-z0-9*/?,\-]+", field):
                raise ValueError("invalid cron field")
        # Kubernetes numbers Sunday as 0; croniter also accepts 7.
        for component in fields[4].split(","):
            if any(int(number) > 6 for number in re.findall(r"\d+", component.split("/")[0])):
                raise ValueError("day-of-week numbers must be between 0 and 6")
        if not croniter.is_valid(expression.replace("?", "*")):
            raise ValueError("invalid cron schedule")
        return value.strip()

    @field_validator("timeZone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        """Validate an IANA timezone; offsets and embedded TZ directives are not accepted."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timeZone must be an IANA timezone name") from exc
        return value

    @property
    def expression(self) -> str:
        return DESCRIPTORS.get(self.schedule.lower(), self.schedule).replace("?", "*")

    def next_time(self, after: dt.datetime) -> dt.datetime:
        """Return the next strictly later matching instant in UTC."""
        local = utc(after).astimezone(ZoneInfo(self.timeZone))
        iterator = croniter(self.expression, local, max_years_between_matches=8)
        for _ in range(100):
            candidate = iterator.get_next(dt.datetime)
            # croniter shifts nonexistent wall times into the next valid hour.
            # CronJob schedules instead skip those occurrences. Validate the
            # round-tripped wall clock before accepting a library candidate.
            instant = utc(candidate)
            wall = instant.astimezone(ZoneInfo(self.timeZone))
            if instant > utc(after) and croniter.match(self.expression, wall.replace(tzinfo=None)):
                return instant
        raise ValueError("Unable to find a valid scheduled instant")

    def due_time(self, after: dt.datetime, now: dt.datetime) -> tuple[dt.datetime | None, str | None]:
        """Select the most recent missed occurrence, with the Kubernetes 100-run bound."""
        after, now = utc(after), utc(now)
        if self.suspend:
            return None, None
        if self.startingDeadlineSeconds is not None:
            after = max(after, now - dt.timedelta(seconds=self.startingDeadlineSeconds, microseconds=1))
        latest = None
        for _ in range(101):
            candidate = self.next_time(after)
            if candidate > now:
                return latest, None
            latest, after = candidate, candidate
        return None, "Too many missed start times (more than 100); set startingDeadlineSeconds or update the schedule"


class ScheduleDefinition(BaseModel):
    """An Evolve policy target plus its portable CronJob timing specification."""

    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")
    spec: CronJobSpec
    dry_run: bool = True
    agent_id: str | None = Field(default=None, min_length=1)
