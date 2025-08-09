from __future__ import annotations
from datetime import date, datetime, timedelta
import calendar
from typing import Iterable, Iterator


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def month_diff(a: date, b: date) -> int:
    # whole months a->b; negative if b<a
    md = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        md -= 1
    return md


def weekly_series(anchor: date, start: date, end: date, step_days: int) -> Iterator[date]:
    # step_days=7 for weekly, 14 for biweekly
    k = max(0, (start - anchor).days // step_days)
    d = anchor + timedelta(days=k * step_days)
    if d < start:
        d += timedelta(days=step_days)
    while d <= end:
        yield d
        d += timedelta(days=step_days)


def monthly_series(anchor: date, start: date, end: date, step_months: int) -> Iterator[date]:
    k = max(0, month_diff(anchor, start))
    d = add_months(anchor, k)
    # ensure d >= start respecting day-of-month rule
    while d < start:
        k += 1
        d = add_months(anchor, k)
    while d <= end:
        yield d
        k += step_months
        d = add_months(anchor, k)


def semi_monthly_series(anchor: date, start: date, end: date) -> Iterator[date]:
    """
    Definition: 1st and 15th of each month (clamped if month shorter).
    If you prefer anchor & anchor+15, switch logic accordingly.
    """
    # iterate months covering the window
    first_month = date(start.year, start.month, 1)
    d = first_month
    while d <= end:
        last = calendar.monthrange(d.year, d.month)[1]
        for day in (1, min(15, last)):
            occ = date(d.year, d.month, day)
            if start <= occ <= end:
                yield occ
        d = add_months(d, 1)


def occurrences_between(anchor: date, frequency: str, start: date, end: date) -> list[date]:
    freq = frequency.strip().lower()
    if start > end:
        return []
    if freq == "weekly":
        it = weekly_series(anchor, start, end, 7)
    elif freq == "biweekly":
        it = weekly_series(anchor, start, end, 14)
    elif freq == "monthly":
        it = monthly_series(anchor, start, end, 1)
    elif freq == "quarterly":
        it = monthly_series(anchor, start, end, 3)
    elif freq == "semi annually":
        it = monthly_series(anchor, start, end, 6)
    elif freq == "annually":
        it = monthly_series(anchor, start, end, 12)
    elif freq == "semi monthly":
        it = semi_monthly_series(anchor, start, end)
    else:
        # unknown frequency: return nothing
        return []
    return list(it)
