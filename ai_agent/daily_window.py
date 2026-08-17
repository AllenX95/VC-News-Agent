"""Shared Beijing-time window for the 10:00 daily crawl and report."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .config import TZ


DAILY_CUTOFF_HOUR = 10


def as_beijing_naive(value: datetime) -> datetime:
    """Normalize a timestamp to the naive Beijing form used by SQLite."""

    if value.tzinfo is None:
        return value
    return value.astimezone(TZ).replace(tzinfo=None)


def daily_window_for_date(target_date: date) -> tuple[datetime, datetime]:
    """Return ``[previous-day 10:00, target-day 10:00)`` in Beijing time."""

    end = datetime.combine(target_date, time(hour=DAILY_CUTOFF_HOUR))
    return end - timedelta(days=1), end


def daily_window_for_run(run_timestamp: datetime) -> tuple[datetime, datetime]:
    """Return the Beijing daily window assigned to a run's calendar date."""

    return daily_window_for_date(as_beijing_naive(run_timestamp).date())


def in_daily_window(value: datetime | None, start: datetime, end: datetime) -> bool:
    """Use a half-open interval so a boundary item appears in exactly one run."""

    if value is None:
        return False
    normalized = as_beijing_naive(value)
    return start <= normalized < end


def in_daily_window_with_precision(
    value: datetime | None,
    precision: str | None,
    start: datetime,
    end: datetime,
) -> bool:
    """Include date-only rows conservatively when their exact time is unknown."""

    if value is None:
        return False
    normalized = as_beijing_naive(value)
    if (precision or "").lower() == "date_only":
        return start.date() <= normalized.date() <= end.date()
    return start <= normalized < end


__all__ = [
    "DAILY_CUTOFF_HOUR",
    "as_beijing_naive",
    "daily_window_for_date",
    "daily_window_for_run",
    "in_daily_window",
    "in_daily_window_with_precision",
]
