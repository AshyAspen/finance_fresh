from tests import helpers  # noqa: F401  # ensure project root on path

from datetime import date, datetime, timedelta
import itertools
import json
import math
import random

from budget import cli
from budget.models import IrregularCategory, IrregularRule, Transaction
from budget.services_irregular import (
    learn_irregular_state,
    forecast_irregular,
    irregular_daily_series,
)


def test_learn_state_minimum():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat = IrregularCategory(name="Groceries")
    session.add(cat)
    session.commit()
    session.add(
        IrregularRule(category_id=cat.id, account_id=cat.account_id, pattern="walmart")
    )
    session.commit()

    random.seed(0)
    ts = datetime(2024, 1, 1)
    txns = []
    for i in range(10):
        txns.append(
            Transaction(
                description="Walmart Supercenter",
                amount=-50.0 - i,
                timestamp=ts,
            )
        )
        ts += timedelta(days=random.randint(3, 7))
    session.add_all(txns)
    session.commit()

    start = txns[0].timestamp.date()
    end = txns[-1].timestamp.date()
    state = learn_irregular_state(session, cat.id, start, end)

    assert 3 <= (state.avg_gap_days or 0) <= 10
    assert (state.median_amount or 0) > 0
    probs = json.loads(state.weekday_probs)
    assert len(probs) == 7
    assert all(p > 0 for p in probs)
    assert math.isclose(sum(probs), 1.0, rel_tol=1e-9)

    session.close()
    db_path.unlink()


def test_forecast_deterministic():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat = IrregularCategory(name="Groceries")
    session.add(cat)
    session.commit()

    state = cat.state
    state.avg_gap_days = 7
    state.median_amount = 40.0
    state.last_event_at = datetime(2024, 1, 1)
    session.add(state)
    session.commit()

    start = date(2024, 1, 2)
    end = start + timedelta(days=59)
    forecast = forecast_irregular(session, cat.id, start, end, mode="deterministic")

    horizon = (end - start).days + 1
    events = {d: amt for d, amt in forecast}
    series = [events.get(start + timedelta(days=i), 0.0) for i in range(horizon)]

    assert len(series) == horizon
    assert sum(series) > 0
    assert sum(1 for amt in series if amt) >= 3

    session.close()
    db_path.unlink()


def test_forecast_mc_quantiles():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat = IrregularCategory(name="Groceries")
    session.add(cat)
    session.commit()

    state = cat.state
    state.avg_gap_days = 7
    state.median_amount = 40.0
    state.last_event_at = datetime(2024, 1, 1)
    state.amount_mu = math.log(40.0)
    state.amount_sigma = 0.1
    session.add(state)
    session.commit()

    start = date(2024, 1, 2)
    end = start + timedelta(days=59)
    random.seed(0)
    forecast = forecast_irregular(
        session, cat.id, start, end, mode="monte_carlo", n=200
    )

    horizon = (end - start).days + 1
    assert len(forecast["p50"]) == len(forecast["p80"]) == horizon

    total_p50 = total_p80 = 0.0
    for i in range(horizon):
        d_expected = start + timedelta(days=i)
        d50, v50 = forecast["p50"][i]
        d80, v80 = forecast["p80"][i]
        assert d50 == d80 == d_expected
        assert v80 >= v50
        total_p50 += v50
        total_p80 += v80
    assert total_p80 >= total_p50 >= 0

    session.close()
    db_path.unlink()


def test_irregular_daily_series():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat = IrregularCategory(name="Groceries")
    session.add(cat)
    session.commit()

    state = cat.state
    state.avg_gap_days = 7
    state.median_amount = 40.0
    state.last_event_at = datetime(2024, 1, 1)
    session.add(state)
    session.commit()

    start = date(2024, 1, 2)
    end = start + timedelta(days=59)

    daily = dict(irregular_daily_series(session, start, end, account_id=cat.account_id))
    horizon = (end - start).days + 1
    series = [daily.get(start + timedelta(days=i), 0.0) for i in range(horizon)]

    assert len(series) == horizon
    assert any(amt > 0 for amt in series)

    expected = dict(forecast_irregular(session, cat.id, start, end))
    for d, amt in expected.items():
        assert daily.get(d, 0.0) == amt

    session.close()
    db_path.unlink()


def test_merge_into_ledger(monkeypatch):
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    fixed_today = date(2024, 1, 1)

    class FakeDate(date):
        @classmethod
        def today(cls):
            return fixed_today

    monkeypatch.setattr(cli, "date", FakeDate)
    monkeypatch.setattr(cli, "IRREG_MODE", "deterministic")

    cat = IrregularCategory(name="Groceries")
    session.add(cat)
    session.commit()

    state = cat.state
    state.avg_gap_days = 7
    state.median_amount = 40.0
    state.last_event_at = datetime.combine(
        fixed_today - timedelta(days=1), datetime.min.time()
    )
    session.add(state)
    session.commit()

    start = fixed_today
    end = start + timedelta(days=30)
    forecast = dict(
        irregular_daily_series(session, start, end, account_id=cat.account_id)
    )

    rows = list(itertools.islice(cli.ledger_rows(session), len(forecast)))
    irregular_rows = {(r.date, r.amount) for r in rows if r.description == "Irregular"}

    for d, amt in forecast.items():
        assert (d, -amt) in irregular_rows

    session.close()
    db_path.unlink()
