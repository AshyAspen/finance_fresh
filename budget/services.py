from __future__ import annotations
from datetime import date, datetime, timedelta
import calendar
import uuid
from typing import Iterable, Iterator

from .models import Account, Balance, Recurring, Transaction
from .services_irregular import irregular_daily_series


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


def create_transaction(
    session,
    account_id: int,
    description: str,
    amount: float,
    when: datetime,
) -> Transaction:
    txn = Transaction(
        account_id=account_id,
        description=description,
        amount=amount,
        timestamp=when,
    )
    session.add(txn)
    session.commit()
    return txn


def create_transfer(
    session,
    from_account_id: int,
    to_account_id: int,
    amount: float,
    when: datetime,
    description: str = "Transfer",
) -> str:
    """Create a two-leg transfer between accounts and return the transfer id."""

    tid = str(uuid.uuid4())
    from_acct = session.get(Account, from_account_id)
    out_desc = description
    in_desc = description
    if description.strip().lower() == "transfer" and from_acct:
        in_desc = f"Transfer from {from_acct.name}"
    t_out = Transaction(
        account_id=from_account_id,
        amount=-abs(amount),
        description=out_desc,
        timestamp=when,
        transfer_id=tid,
    )
    t_in = Transaction(
        account_id=to_account_id,
        amount=abs(amount),
        description=in_desc,
        timestamp=when,
        transfer_id=tid,
    )
    session.add_all([t_out, t_in])
    session.commit()
    return tid


def delete_transfer(session, transfer_id: str) -> None:
    txns = session.query(Transaction).filter(Transaction.transfer_id == transfer_id).all()
    for t in txns:
        session.delete(t)
    session.commit()


def update_transfer(
    session,
    transfer_id: str,
    description: str,
    amount: float,
    when: datetime,
) -> None:
    txns = session.query(Transaction).filter(Transaction.transfer_id == transfer_id).all()
    for t in txns:
        t.description = description
        t.timestamp = when
        if t.amount < 0:
            t.amount = -abs(amount)
        else:
            t.amount = abs(amount)
    session.commit()


def simulate_balances(
    session,
    start_date: date,
    end_date: date,
    account_ids: list[int],
    candidate_extra: dict[int, float],
) -> dict[int, list[tuple[date, float]]]:
    """Return per-account daily balances applying an extra payment today."""

    # Latest balance snapshot per account
    bal_map: dict[int, tuple[float, datetime]] = {}
    for aid in account_ids:
        row = (
            session.query(Balance)
            .filter(Balance.account_id == aid)
            .order_by(Balance.timestamp.desc())
            .first()
        )
        amt = row.amount if row else 0.0
        ts = (
            row.timestamp
            if row and row.timestamp
            else datetime.combine(date.today(), datetime.min.time())
        )
        bal_map[aid] = (amt, ts)

    # Starting balances after posted transactions up to start_date
    start_balances: dict[int, float] = {}
    start_dt = datetime.combine(start_date, datetime.max.time())
    for aid, (bal_amt, bal_ts) in bal_map.items():
        posted_rows = (
            session.query(Transaction.amount)
            .filter(
                Transaction.account_id == aid,
                Transaction.timestamp > bal_ts,
                Transaction.timestamp <= start_dt,
            )
            .all()
        )
        posted_sum = sum(a for (a,) in posted_rows)
        start_balances[aid] = bal_amt + posted_sum + candidate_extra.get(aid, 0.0)

    # Collect future events
    events: list[Transaction] = []
    end_dt = datetime.combine(end_date, datetime.max.time())
    txns = (
        session.query(Transaction)
        .filter(
            Transaction.account_id.in_(account_ids),
            Transaction.timestamp > start_dt,
            Transaction.timestamp <= end_dt,
        )
        .order_by(Transaction.timestamp)
        .all()
    )
    events.extend(txns)

    recs = session.query(Recurring).filter(Recurring.account_id.in_(account_ids)).all()
    for r in recs:
        anchor = r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
        occs = occurrences_between(anchor, r.frequency, start_date, end_date)
        for occ in occs:
            events.append(
                Transaction(
                    account_id=r.account_id,
                    amount=r.amount,
                    description=r.description,
                    timestamp=datetime.combine(occ, datetime.min.time()),
                )
            )

    for aid in account_ids:
        irr = irregular_daily_series(
            session,
            start_date,
            end_date,
            account_id=aid,
            mode="deterministic",
            quantile="p80",
        )
        for d, amt in irr:
            if amt:
                events.append(
                    Transaction(
                        account_id=aid,
                        amount=-amt,
                        description="Irregular",
                        timestamp=datetime.combine(d, datetime.min.time()),
                    )
                )

    events.sort(
        key=lambda t: (
            t.timestamp,
            t.account_id,
            t.description or "",
            t.amount,
        )
    )

    balances = {aid: [] for aid in account_ids}
    running = dict(start_balances)
    idx = 0
    curr = start_date
    while curr <= end_date:
        while idx < len(events) and events[idx].timestamp.date() <= curr:
            t = events[idx]
            running[t.account_id] = running.get(t.account_id, 0.0) + t.amount
            idx += 1
        for aid in account_ids:
            balances[aid].append((curr, running.get(aid, 0.0)))
        curr += timedelta(days=1)

    return balances


def max_safe_payment_today(
    session,
    target_account_id: int,
    buffer_by_account: dict[int, float],
    horizon_days: int = 120,
) -> float:
    """Return max extra payment today without dipping below buffers."""

    start = date.today()
    end = start + timedelta(days=horizon_days)
    account_ids = list(buffer_by_account.keys())

    baseline = simulate_balances(session, start, end, account_ids, {})
    start_balance = next(b for d, b in baseline[target_account_id] if d == start)
    hi = max(0.0, start_balance - buffer_by_account.get(target_account_id, 0.0))
    lo = 0.0

    def ok(x: float) -> bool:
        cand = {target_account_id: -x}
        sim = simulate_balances(session, start, end, account_ids, cand)
        for aid, series in sim.items():
            buf = buffer_by_account.get(aid, 0.0)
            if any(bal < buf for _, bal in series):
                return False
        return True

    while hi - lo > 0.01:
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)
