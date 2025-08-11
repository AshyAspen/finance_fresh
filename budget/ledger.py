from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
import calendar
from bisect import bisect_right
from collections import defaultdict

from .models import Transaction, Recurring, Balance, Account
from .database import ensure_default_account
from .services import (
    add_months,
    get_first_balance,
    get_last_checkpoint,
    earliest_posted_tx_date,
    occurrences_between,
    _posted_index_for_window,
    _overlaps_posted,
)
from .services_irregular import irregular_daily_series
from sqlalchemy import func

def last_reconcile_date(session, account_id: int) -> date:
    cp = get_last_checkpoint(session, account_id)
    if cp:
        return cp.as_of_date
    fb = get_first_balance(session, account_id)
    if fb:
        return fb.timestamp.date()
    e = earliest_posted_tx_date(session, account_id)
    return e or date.today()


def projected_balance_on(session, account_id: int, as_of: date, irreg_mode: str = "monte_carlo", irreg_quantile: str = "p80") -> float:
    start_d = last_reconcile_date(session, account_id)
    rows = [
        r
        for r in ledger_rows(session, start_d, as_of, account_ids=[account_id], irreg_mode=irreg_mode, irreg_quantile=irreg_quantile)
        if r.timestamp.date() <= as_of
    ]
    return rows[-1].running_account if rows else 0.0


def display_balance_as_of(session, account_id: int, as_of: date, irreg_mode: str = "monte_carlo", irreg_quantile: str = "p80") -> float:
    """Return running balance for account at ``as_of`` using ledger rules."""
    rows = [r for r in ledger_rows(session, as_of, as_of, account_ids=[account_id], irreg_mode=irreg_mode, irreg_quantile=irreg_quantile)]
    if not rows:
        fb = get_first_balance(session, account_id)
        start_d = fb.timestamp.date() if fb else (
            earliest_posted_tx_date(session, account_id) or as_of
        )
        rows = [
            r
            for r in ledger_rows(session, start_d, as_of, account_ids=[account_id], irreg_mode=irreg_mode, irreg_quantile=irreg_quantile)
            if r.timestamp.date() <= as_of
        ]
    return rows[-1].running_account if rows else 0.0


def list_transactions_since_checkpoint(
    session, account_id: int, start_d: date, end_d: date
):
    return (
        session.query(Transaction)
        .filter(Transaction.account_id == account_id)
        .filter(Transaction.timestamp >= datetime.combine(start_d, time.min))
        .filter(Transaction.timestamp <= datetime.combine(end_d, time.max))
        .order_by(Transaction.timestamp.asc())
        .all()
    )

def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def occurrence_on_or_before(start: date, freq: str, target: date) -> date | None:
    if target < start:
        return None
    if freq == "weekly":
        weeks = (target - start).days // 7
        return start + timedelta(weeks=weeks)
    if freq == "biweekly":
        weeks = (target - start).days // 14
        return start + timedelta(weeks=weeks * 2)
    if freq == "semi monthly":
        days = (target - start).days // 15
        return start + timedelta(days=days * 15)
    step_map = {"monthly": 1, "quarterly": 3, "semi annually": 6, "annually": 12}
    step = step_map.get(freq)
    if step:
        months = months_between(start, target)
        months = (months // step) * step
        occ = add_months(start, months)
        if occ > target:
            months -= step
            if months < 0:
                return None
            occ = add_months(start, months)
        return occ
    return start


def occurrence_after(start: date, freq: str, after: date) -> date | None:
    if after < start:
        return start
    if freq == "weekly":
        weeks = (after - start).days // 7 + 1
        return start + timedelta(weeks=weeks)
    if freq == "biweekly":
        weeks = (after - start).days // 14 + 1
        return start + timedelta(weeks=weeks * 2)
    if freq == "semi monthly":
        days = (after - start).days // 15 + 1
        return start + timedelta(days=days * 15)
    step_map = {"monthly": 1, "quarterly": 3, "semi annually": 6, "annually": 12}
    step = step_map.get(freq)
    if step:
        months = months_between(start, after)
        # round down to the nearest step interval
        months = (months // step) * step
        occ = add_months(start, months)
        # if the computed occurrence is not strictly after the target date,
        # advance by one additional interval
        if occ <= after:
            months += step
        return add_months(start, months)
    return None
def next_event(after: datetime, txns, recs):
    next_txn = None
    txn_times = [t.timestamp for t in txns]
    idx = bisect_right(txn_times, after)
    if idx < len(txns):
        next_txn = txns[idx]
    next_rec = None
    next_rec_time = None
    for i, r in enumerate(recs):
        occ = occurrence_on_or_before(r.start_date.date(), r.frequency, after.date())
        if occ is not None:
            occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(
                microseconds=i
            )
            if occ_dt > after and (next_rec_time is None or occ_dt < next_rec_time):
                next_rec_time = occ_dt
                next_rec = r
                continue
        occ = occurrence_after(r.start_date.date(), r.frequency, after.date())
        if occ is None:
            continue
        occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(microseconds=i)
        if next_rec_time is None or occ_dt < next_rec_time:
            next_rec_time = occ_dt
            next_rec = r
    if next_txn is None and next_rec is None:
        return None
    if next_txn is not None and (
        next_rec_time is None or next_txn.timestamp <= next_rec_time
    ):
        return next_txn.timestamp, next_txn.description, next_txn.amount
    return next_rec_time, next_rec.description, next_rec.amount
@dataclass
class LedgerRow:
    timestamp: datetime
    account_id: int
    description: str
    amount: float
    running_account: float
    running_total: float

    @property
    def date(self) -> date:
        return self.timestamp.date()

    @property
    def running(self) -> float:
        return self.running_account

def end_of_month(d: date, months: int = 0) -> date:
    d = add_months(d, months)
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)

def ledger_rows(
    session,
    plan_start: date | None = None,
    plan_end: date | None = None,
    account_ids: list[int] | None = None,
    irreg_mode: str = "monte_carlo",
    irreg_quantile: str = "p80",
    today: date | None = None,
):
    today = today or date.today()
    if account_ids is None:
        ids = set()
        ids.update(a for (a,) in session.query(Transaction.account_id).distinct())
        ids.update(a for (a,) in session.query(Recurring.account_id).distinct())
        ids.update(a for (a,) in session.query(Balance.account_id).distinct())
        account_ids = sorted(ids)
    if not account_ids:
        account_ids = [ensure_default_account(session).id]

    first_bal_ts: dict[int, datetime] = {}
    first_bal_amt: dict[int, float] = {}
    earliest_tx_d: dict[int, date | None] = {}
    for aid in account_ids:
        fb = get_first_balance(session, aid)
        if fb:
            first_bal_ts[aid] = fb.timestamp
            first_bal_amt[aid] = fb.amount or 0.0
        else:
            first_bal_ts[aid] = datetime.combine(today or date.today(), time.min)
            first_bal_amt[aid] = 0.0
        earliest_tx_d[aid] = earliest_posted_tx_date(session, aid)

    min_bal_date = min(ts.date() for ts in first_bal_ts.values())

    if plan_start is None or plan_end is None:
        earliest_tx = (
            session.query(Transaction)
            .filter(Transaction.account_id.in_(account_ids))
            .order_by(Transaction.timestamp)
            .first()
        )
        earliest_date = earliest_tx.timestamp.date() if earliest_tx else min_bal_date
        plan_start = min(earliest_date, min_bal_date)
        plan_end = (today or date.today()) + timedelta(days=3650)  # ~10 years

    posted_idx = _posted_index_for_window(session, account_ids, plan_start, today or date.today())

    txns = (
        session.query(Transaction)
        .filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp)
        .all()
    )

    recs = session.query(Recurring).filter(Recurring.account_id.in_(account_ids)).all()
    synthetic_txns: list[Transaction] = []
    for r in recs:
        anchor = (
            r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
        )
        occs = occurrences_between(anchor, r.frequency, plan_start, plan_end)
        lower_bound = max(
            plan_start, earliest_tx_d[r.account_id] or first_bal_ts[r.account_id].date()
        )
        for occ in occs:
            if occ < lower_bound:
                continue
            synthetic_txns.append(
                Transaction(
                    description=r.description,
                    amount=r.amount,
                    timestamp=datetime.combine(occ, datetime.min.time()),
                    account_id=r.account_id,
                )
            )
    txns.extend(synthetic_txns)

    irr_start = max(today or date.today(), min_bal_date)
    irr_series: list[Transaction] = []
    for aid in account_ids:
        irr_forecast = irregular_daily_series(
            session,
            irr_start,
            plan_end,
            account_id=aid,
            mode=irreg_mode,
            quantile=irreg_quantile,
        )
        lower_bound = max(plan_start, earliest_tx_d[aid] or first_bal_ts[aid].date())
        for d, amt in irr_forecast:
            if d < lower_bound:
                continue
            if amt:
                irr_series.append(
                    Transaction(
                        description="Irregular",
                        amount=-amt,
                        timestamp=datetime.combine(d, datetime.min.time()),
                        account_id=aid,
                    )
                )
    txns.extend(irr_series)

    for t in synthetic_txns:
        setattr(t, "_source_type", "recurring")
    for t in irr_series:
        setattr(t, "_source_type", "irregular")

    def _synthetic_overlaps_posted(t: Transaction) -> bool:
        return _overlaps_posted(
            posted_idx,
            t.account_id,
            t.timestamp.date(),
            t.amount or 0.0,
            t.description or "",
        )

    filtered: list[Transaction] = []
    for t in txns:
        src = getattr(t, "_source_type", "posted")
        if src != "posted" and _synthetic_overlaps_posted(t):
            continue
        filtered.append(t)
    txns = filtered

    def classify_priority(t):
        src = getattr(t, "_source_type", "posted")
        amt = t.amount or 0.0
        if src == "irregular":
            return (50, 0)
        if src == "recurring":
            return (20, 0) if amt > 0 else (30, 0)
        return (20, 0) if amt > 0 else (40, 0)

    def _ledger_sort_key(t: Transaction):
        prio, rank = classify_priority(t)
        date_key = t.timestamp.date()
        src_key = getattr(t, "_source_type", "posted") or "posted"
        acct_key = t.account_id if t.account_id is not None else -1
        id_key = t.id if t.id is not None else 0
        desc_key = t.description or ""
        amt_key = 0.0 if t.amount is None else float(f"{abs(t.amount):.2f}")
        return (
            date_key,
            prio,
            rank,
            src_key,
            acct_key,
            id_key,
            desc_key,
            amt_key,
            t.timestamp,
        )

    for t in txns:
        assert t.timestamp is not None
        assert isinstance(t.account_id if t.account_id is not None else -1, int)

    txns.sort(key=_ledger_sort_key)

    offset: dict[int, float] = {}
    for aid in account_ids:
        bal_ts = first_bal_ts[aid]
        bal_amt = first_bal_amt[aid]
        total_before = 0.0
        for t in txns:
            if (
                t.account_id == aid
                and t.timestamp <= bal_ts
                and getattr(t, "_source_type", "posted") == "posted"
            ):
                total_before += t.amount or 0.0
        offset[aid] = bal_amt - total_before

    running_by_acct: defaultdict[int, float] = defaultdict(float)
    last_ts: datetime | None = None
    bump = 0
    for t in txns:
        if not (plan_start <= t.timestamp.date() <= plan_end):
            continue
        aid = t.account_id
        ts_d = t.timestamp.date()
        src = getattr(t, "_source_type", "posted")
        first_ts_d = first_bal_ts[aid].date()
        if ts_d < first_ts_d:
            eff = t.amount or 0.0
        elif first_ts_d <= ts_d <= (today or date.today()):
            if src == "posted":
                eff = t.amount or 0.0
            else:
                eff = 0.0 if _synthetic_overlaps_posted(t) else (t.amount or 0.0)
        else:
            eff = t.amount or 0.0

        if t.timestamp == last_ts:
            bump += 1
        else:
            last_ts = t.timestamp
            bump = 0

        running_by_acct[aid] += eff
        display_running = running_by_acct[aid] + offset[aid]
        running_total = sum(running_by_acct.values()) + sum(offset.values())
        yield LedgerRow(
            t.timestamp + timedelta(microseconds=bump),
            t.account_id,
            t.description,
            t.amount,
            display_running,
            running_total,
        )

