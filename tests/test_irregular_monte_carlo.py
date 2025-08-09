from tests import helpers  # noqa: F401  # ensure project root on path

from datetime import date, datetime, timedelta
import json
import math
import random

from budget import cli
from budget.models import IrregularCategory, Balance
from budget.services_irregular import forecast_irregular


def test_forecast_irregular_monte_carlo():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat = IrregularCategory(name="Auto")
    session.add(cat)
    session.commit()

    state = cat.state
    state.avg_gap_days = 5
    state.median_amount = 100.0
    state.last_event_at = datetime(2023, 12, 31)
    state.weekday_probs = json.dumps([1, 1, 1, 1, 1, 1, 1])
    state.amount_mu = math.log(100.0)
    state.amount_sigma = 0.1
    session.add(state)
    session.commit()

    start = date(2024, 1, 1)
    end = date(2024, 1, 31)

    random.seed(0)
    forecast = forecast_irregular(
        session, cat.id, start, end, mode="monte_carlo", n=100
    )

    horizon = (end - start).days + 1
    assert all(len(forecast[p]) == horizon for p in ("p50", "p80", "p90"))
    for i in range(horizon):
        d_expected = start + timedelta(days=i)
        d50, v50 = forecast["p50"][i]
        d80, v80 = forecast["p80"][i]
        d90, v90 = forecast["p90"][i]
        assert d50 == d80 == d90 == d_expected
        assert v80 >= v50
        assert v90 >= v80
    assert any(v > 0 for _, v in forecast["p90"])

    session.close()
    db_path.unlink()


def test_monte_carlo_matches_deterministic(monkeypatch):
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()
    try:
        session.add(Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)))
        cat = IrregularCategory(name="Auto")
        session.add(cat)
        session.commit()
        state = cat.state
        state.avg_gap_days = 5
        state.median_amount = 100.0
        state.last_event_at = datetime(2023, 1, 1)
        state.amount_mu = math.log(100.0)
        state.amount_sigma = 0.0
        state.weekday_probs = None
        session.add(state)
        session.commit()

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2023, 1, 1)

        monkeypatch.setattr(cli, "date", FakeDate)

        start = date(2023, 1, 1)
        end = date(2023, 1, 31)

        monkeypatch.setattr(cli, "IRREG_MODE", "deterministic")
        rows_det = list(cli.ledger_rows(session, start, end))

        import budget.services_irregular as irr

        monkeypatch.setattr(irr.random, "gauss", lambda mu, sigma: mu)
        monkeypatch.setattr(cli, "IRREG_MODE", "monte_carlo")
        rows_mc = list(cli.ledger_rows(session, start, end))

        assert [
            (r.date, r.description, round(r.amount, 2), round(r.running, 2))
            for r in rows_mc
        ] == [
            (r.date, r.description, round(r.amount, 2), round(r.running, 2))
            for r in rows_det
        ]
    finally:
        session.close()
        db_path.unlink()
