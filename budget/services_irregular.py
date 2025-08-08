from datetime import date, datetime, timedelta
import json
import math
import random
from statistics import mean, median, stdev
from typing import Iterable, Literal

from sqlalchemy.orm import Session, object_session

from .models import IrregularCategory, IrregularState, IrregularRule, Transaction


def ensure_category(session: Session, name: str, **kwargs) -> IrregularCategory: ...


def get_or_create_state(session: Session, category_id: int) -> IrregularState:
    """Return the :class:`IrregularState` for ``category_id`` creating it if missing."""

    state = (
        session.query(IrregularState)
        .filter(IrregularState.category_id == category_id)
        .one_or_none()
    )
    if state is None:
        state = IrregularState(category_id=category_id)
        session.add(state)
        session.commit()
    return state

def learn_irregular_state(
    session: Session,
    category_id: int,
    start: date | datetime,
    end: date | datetime,
) -> IrregularState:
    """Learn timing and amount stats for an irregular category.

    Parameters
    ----------
    session : Session
        Database session.
    category_id : int
        Target irregular category.
    start, end : date | datetime
        Period to learn from. The start date will be clamped to the
        category's ``window_days`` ending at ``end``.

    Returns
    -------
    IrregularState
        The upserted state object for the category.
    """

    # Fetch category to obtain learning parameters like window_days and alpha
    category: IrregularCategory | None = session.get(IrregularCategory, category_id)
    if category is None:
        raise ValueError(f"Unknown category id {category_id}")

    # Clamp start to the category's window
    end_dt = end if isinstance(end, datetime) else datetime.combine(end, datetime.min.time())
    start_dt = (
        start if isinstance(start, datetime) else datetime.combine(start, datetime.min.time())
    )

    window_start = end_dt - timedelta(days=category.window_days)
    if start_dt < window_start:
        start_dt = window_start

    # Fetch or create state row
    state = (
        session.query(IrregularState)
        .filter(IrregularState.category_id == category_id)
        .one_or_none()
    )
    if state is None:
        state = IrregularState(category_id=category_id)
        session.add(state)

    # Fetch transactions in window and matching category rules
    txns = (
        session.query(Transaction)
        .filter(
            Transaction.timestamp >= start_dt,
            Transaction.timestamp <= end_dt,
        )
        .order_by(Transaction.timestamp)
        .all()
    )
    txns = [t for t in txns if match_category_id(session, t.description) == category_id]

    if not txns:
        session.commit()
        return state

    amounts = [abs(t.amount) for t in txns if abs(t.amount) > 0]
    median_amount = median(amounts) if amounts else None
    last_event_at = max(t.timestamp for t in txns)

    # If fewer than 3 transactions, only update last_event_at and median_amount
    if len(txns) < 3:
        state.median_amount = median_amount
        state.last_event_at = last_event_at
        session.add(state)
        session.commit()
        return state

    timestamps = [t.timestamp for t in txns]
    gaps = [
        (timestamps[i] - timestamps[i - 1]).total_seconds() / 86400.0
        for i in range(1, len(timestamps))
    ]

    if len(gaps) >= 3:
        avg_gap = gaps[0]
        for g in gaps[1:]:
            avg_gap = category.alpha * g + (1 - category.alpha) * avg_gap
    else:
        avg_gap = mean(gaps)

    counts = [1] * 7
    for t in txns:
        counts[t.timestamp.weekday()] += 1
    total = sum(counts)
    weekday_probs = [c / total for c in counts]

    if len(amounts) >= 8:
        logs = [math.log(a) for a in amounts if a > 0]
        mu = mean(logs)
        sigma = stdev(logs) if len(logs) > 1 else 0.0
        state.amount_mu = mu
        state.amount_sigma = sigma
    else:
        state.amount_mu = None
        state.amount_sigma = None

    state.avg_gap_days = avg_gap
    state.weekday_probs = json.dumps(weekday_probs)
    state.median_amount = median_amount
    state.last_event_at = last_event_at

    session.add(state)
    session.commit()
    return state


def update_irregular_state(state: IrregularState, new_tx: Transaction) -> IrregularState:
    """Update an existing irregular ``state`` with a new transaction.

    Parameters
    ----------
    state : IrregularState
        The state object to update. It must already be associated with a
        database session.
    new_tx : Transaction
        The newly observed transaction belonging to the state's category.
    """

    # Update average gap between events using exponential smoothing
    if state.last_event_at is not None:
        new_gap = (new_tx.timestamp - state.last_event_at).total_seconds() / 86400.0
        if new_gap >= 1.0:
            alpha = state.category.alpha if state.category else 0.3
            if state.avg_gap_days is None:
                state.avg_gap_days = new_gap
            else:
                state.avg_gap_days = alpha * new_gap + (1 - alpha) * state.avg_gap_days

    # Update median amount with a simple moving approximation
    amount = abs(new_tx.amount)
    if amount > 0:
        alpha = state.category.alpha if state.category else 0.3
        if state.median_amount is None:
            state.median_amount = amount
        else:
            state.median_amount = alpha * amount + (1 - alpha) * state.median_amount

    # Update weekday probabilities by incrementing the observed weekday
    try:
        probs = json.loads(state.weekday_probs) if state.weekday_probs else None
        if not (isinstance(probs, list) and len(probs) == 7):
            probs = [1 / 7.0] * 7
    except Exception:  # pragma: no cover - defensive
        probs = [1 / 7.0] * 7

    counts = [p for p in probs]
    counts[new_tx.timestamp.weekday()] += 1
    total = sum(counts)
    probs = [c / total for c in counts]
    state.weekday_probs = json.dumps(probs)

    # Update timestamp of last event
    state.last_event_at = new_tx.timestamp

    sess = object_session(state)
    if sess is not None:
        sess.add(state)
        sess.commit()

    return state

def forecast_irregular(
    session: Session,
    category_id: int,
    start: date,
    end: date,
    mode: Literal["deterministic", "monte_carlo"] = "deterministic",
    n: int = 500,
):
    """Forecast irregular events for a category.

    The forecast is produced using the learned :class:`IrregularState` for the
    category. When the state is missing or incomplete it will be learned using
    historical transactions. Two modes are supported:

    ``"deterministic"``
        Returns a list of ``(date, amount)`` tuples using the average gap and
        typical amount.

    ``"monte_carlo"``
        Runs ``n`` Monte Carlo simulations and returns percentile series in the
        form ``{"p50": [(date, val)], ...}``.

    Parameters
    ----------
    session : Session
        Database session.
    category_id : int
        Target irregular category.
    start, end : date
        Forecast window (inclusive).
    mode : str, optional
        ``"deterministic"`` by default.
    n : int, optional
        Number of runs for ``"monte_carlo"`` mode.

    Returns
    -------
    list[tuple[date, float]] | dict[str, list[tuple[date, float]]]
        Forecasted events aggregated per day.
    """

    # Fetch the category to obtain parameters like window_days
    category: IrregularCategory | None = session.get(IrregularCategory, category_id)
    if category is None:
        raise ValueError(f"Unknown category id {category_id}")

    # Ensure state exists and has the required statistics. If missing, learn it
    state = (
        session.query(IrregularState)
        .filter(IrregularState.category_id == category_id)
        .one_or_none()
    )
    if (
        state is None
        or state.avg_gap_days is None
        or state.median_amount is None
        or state.last_event_at is None
    ):
        learn_start = end - timedelta(days=category.window_days)
        state = learn_irregular_state(session, category_id, learn_start, end)

    # If we still don't have enough data, nothing to forecast
    if (
        state is None
        or state.avg_gap_days is None
        or (
            state.median_amount is None
            and (state.amount_mu is None or state.amount_sigma is None)
        )
    ):
        if mode == "monte_carlo":
            return {"p50": [], "p80": [], "p90": []}
        return []

    if mode == "deterministic":
        # Determine amount per event. Fall back to mean of recent history if needed
        amount = state.median_amount
        if amount is None:
            end_dt = datetime.combine(end, datetime.min.time())
            start_dt = end_dt - timedelta(days=category.window_days)
            txns = (
                session.query(Transaction)
                .filter(
                    Transaction.timestamp >= start_dt,
                    Transaction.timestamp <= end_dt,
                )
                .order_by(Transaction.timestamp)
                .all()
            )
            txns = [t for t in txns if match_category_id(session, t.description) == category_id]
            amounts = [abs(t.amount) for t in txns if abs(t.amount) > 0]
            amount = mean(amounts) if amounts else None
            if amount is None:
                return []

        # Seed the next event date using last_event_at (or start) and avg gap
        gap_days = math.ceil(state.avg_gap_days if state.avg_gap_days else 7)
        last_event_date = state.last_event_at.date() if state.last_event_at else start
        next_date = last_event_date + timedelta(days=gap_days)

        # Helper for weekday snapping
        weekday_probs = None
        best_weekday = None
        if state.weekday_probs:
            try:
                weekday_probs = json.loads(state.weekday_probs)
                if isinstance(weekday_probs, list) and len(weekday_probs) == 7:
                    best_weekday = max(range(7), key=lambda i: weekday_probs[i])
            except Exception:  # pragma: no cover - defensive
                weekday_probs = None

        def snap_weekday(d: date) -> date:
            if best_weekday is None:
                return d
            while d.weekday() != best_weekday:
                d += timedelta(days=1)
            return d

        next_date = snap_weekday(next_date)

        # Catch up if the seeded date is before the forecast start
        while next_date < start:
            next_date = snap_weekday(next_date + timedelta(days=gap_days))

        events: list[tuple[date, float]] = []
        while next_date <= end:
            events.append((next_date, float(amount)))
            next_date = snap_weekday(next_date + timedelta(days=gap_days))

        # Aggregate by day
        totals: dict[date, float] = {}
        for d, amt in events:
            if start <= d <= end:
                totals[d] = totals.get(d, 0.0) + amt

        return sorted(totals.items(), key=lambda x: x[0])

    elif mode == "monte_carlo":
        horizon = (end - start).days + 1
        runs = [[0.0 for _ in range(horizon)] for _ in range(n)]

        weekday_probs = None
        if state.weekday_probs:
            try:
                weekday_probs = json.loads(state.weekday_probs)
                if not (isinstance(weekday_probs, list) and len(weekday_probs) == 7):
                    weekday_probs = None
            except Exception:  # pragma: no cover - defensive
                weekday_probs = None

        for r in range(n):
            current_date = state.last_event_at.date() if state.last_event_at else start
            while True:
                gap = random.gauss(state.avg_gap_days, 0.35 * state.avg_gap_days)
                gap = max(1.0, gap)
                gap = min(gap, 30.0)
                gap = int(round(gap))
                current_date = current_date + timedelta(days=gap)
                if weekday_probs:
                    target = random.choices(range(7), weights=weekday_probs)[0]
                    while current_date.weekday() != target:
                        current_date += timedelta(days=1)
                if current_date > end:
                    break
                if current_date < start:
                    continue
                if (
                    state.amount_mu is not None
                    and state.amount_sigma is not None
                ):
                    amount = random.lognormvariate(state.amount_mu, state.amount_sigma)
                else:
                    amount = state.median_amount * (1 + random.gauss(0.0, 0.1))
                idx = (current_date - start).days
                runs[r][idx] += float(amount)

        def percentile(values: list[float], p: float) -> float:
            values = sorted(values)
            k = (len(values) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return values[int(k)]
            return values[f] + (values[c] - values[f]) * (k - f)

        dates = [start + timedelta(days=i) for i in range(horizon)]
        result: dict[str, list[tuple[date, float]]] = {"p50": [], "p80": [], "p90": []}
        for i, d in enumerate(dates):
            day_vals = [runs[r][i] for r in range(n)]
            result["p50"].append((d, float(percentile(day_vals, 0.5))))
            result["p80"].append((d, float(percentile(day_vals, 0.8))))
            result["p90"].append((d, float(percentile(day_vals, 0.9))))
        return result

    else:  # pragma: no cover - defensive
        raise NotImplementedError(f"Unknown mode: {mode}")


def irregular_daily_series(
    session: Session,
    start: date,
    end: date,
    mode: str = "deterministic",
    quantile: str = "p80",
) -> list[tuple[date, float]]:
    """Aggregate forecasts for all active irregular categories.

    Parameters
    ----------
    session : Session
        Database session.
    start, end : date
        Inclusive date range for the forecast.
    mode : str, optional
        Forecasting mode passed to :func:`forecast_irregular`. Defaults to
        ``"deterministic"``.
    quantile : str, optional
        When ``mode`` is ``"monte_carlo"``, the percentile series to use.

    Returns
    -------
    list[tuple[date, float]]
        Sorted daily totals of forecasted amounts.
    """

    cats = (
        session.query(IrregularCategory)
        .filter(IrregularCategory.active.is_(True))
        .all()
    )
    totals: dict[date, float] = {}
    for cat in cats:
        forecast = forecast_irregular(session, cat.id, start, end, mode=mode)
        series: Iterable[tuple[date, float]]
        if mode == "monte_carlo":
            series = forecast.get(quantile, [])  # type: ignore[assignment]
        else:
            series = forecast  # type: ignore[assignment]
        for d, amt in series:
            if start <= d <= end and amt:
                totals[d] = totals.get(d, 0.0) + amt
    return sorted(totals.items(), key=lambda x: x[0])


def categories(session: Session) -> list[IrregularCategory]:
    """Return irregular categories ordered by name.

    All categories are returned so the caller can inspect or toggle the
    ``active`` flag as needed.
    """

    return (
        session.query(IrregularCategory)
        .order_by(IrregularCategory.name)
        .all()
    )


def rules_for(session: Session, category_id: int) -> list[str]:
    """Return active rule patterns for the given category."""

    rows = (
        session.query(IrregularRule.pattern)
        .filter(
            IrregularRule.category_id == category_id, IrregularRule.active.is_(True)
        )
        .all()
    )
    return [r[0] for r in rows]


def match_category_id(session: Session, description: str) -> int | None:
    """Return the first matching active category for the description."""

    desc = description.lower()
    rules = (
        session.query(IrregularRule)
        .join(IrregularCategory, IrregularRule.category_id == IrregularCategory.id)
        .filter(IrregularRule.active.is_(True), IrregularCategory.active.is_(True))
        .order_by(IrregularRule.id)
        .all()
    )
    for rule in rules:
        if rule.pattern.lower() in desc:
            return rule.category_id
    return None
