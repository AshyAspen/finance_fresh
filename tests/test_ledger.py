import itertools
from datetime import datetime, date

from tests.helpers import get_temp_session
from budget import cli
from budget.models import Transaction, Balance, Recurring


def test_ledger_running_balance():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=100.0, timestamp=datetime(2023, 1, 2)),
                Transaction(description="T1", amount=-10.0, timestamp=datetime(2023, 1, 1)),
                Transaction(description="T2", amount=20.0, timestamp=datetime(2023, 1, 3)),
            ]
        )
        session.commit()
        rows = list(cli.ledger_rows(session))
        assert rows[0].running == 100.0
        assert rows[1].running == 120.0
    finally:
        session.close()
        path.unlink()


def test_ledger_includes_recurring():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="weekly",
                ),
            ]
        )
        session.commit()
        rows = list(itertools.islice(cli.ledger_rows(session), 2))
        assert rows[0].date == datetime(2023, 1, 1).date()
        assert rows[1].date == datetime(2023, 1, 8).date()
    finally:
        session.close()
        path.unlink()


def test_monthly_recurring_occurs_each_month():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
            ]
        )
        session.commit()
        rows = list(itertools.islice(cli.ledger_rows(session), 2))
        assert rows[0].date == datetime(2023, 1, 1).date()
        assert rows[1].date == datetime(2023, 2, 1).date()
    finally:
        session.close()
        path.unlink()


def test_multiple_events_same_day():
    """Ledger supports multiple events on the same date."""
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 2),
                    frequency="weekly",
                ),
                Transaction(
                    description="Coffee",
                    amount=-5.0,
                    timestamp=datetime(2023, 1, 2, 8),
                ),
                Transaction(
                    description="Lunch",
                    amount=-10.0,
                    timestamp=datetime(2023, 1, 2, 12),
                ),
            ]
        )
        session.commit()
        rows = list(itertools.islice(cli.ledger_rows(session), 3))
        assert [r.description for r in rows] == ["Rent", "Coffee", "Lunch"]
        assert [r.running for r in rows] == [-50.0, -55.0, -65.0]
    finally:
        session.close()
        path.unlink()


def test_multiple_recurring_same_day():
    """Multiple recurring incomes/bills on the same day propagate correctly."""
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2022, 12, 31)),
                Recurring(
                    description="Salary",
                    amount=1000.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
                Recurring(
                    description="Rent",
                    amount=-500.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
            ]
        )
        session.commit()
        rows = list(itertools.islice(cli.ledger_rows(session), 4))
        assert rows[0].date == rows[1].date == date(2023, 1, 1)
        assert {rows[0].description, rows[1].description} == {"Salary", "Rent"}
        assert rows[2].date == rows[3].date == date(2023, 2, 1)
        assert {rows[2].description, rows[3].description} == {"Salary", "Rent"}

        running = 0.0
        for r in rows:
            running += r.amount
            assert r.running == running
    finally:
        session.close()
        path.unlink()


def test_last_day_of_month_propagates_and_recovers():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 31),
                    frequency="monthly",
                ),
            ]
        )
        session.commit()
        rows = list(itertools.islice(cli.ledger_rows(session), 5))
        assert [r.date for r in rows] == [
            date(2023, 1, 31),
            date(2023, 2, 28),
            date(2023, 3, 31),
            date(2023, 4, 30),
            date(2023, 5, 31),
        ]
    finally:
        session.close()
        path.unlink()


def test_ledger_view_displays_all_events(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 2),
                    frequency="weekly",
                ),
                Transaction(
                    description="Coffee",
                    amount=-5.0,
                    timestamp=datetime(2023, 1, 2, 8),
                ),
                Transaction(
                    description="Lunch",
                    amount=-10.0,
                    timestamp=datetime(2023, 1, 2, 12),
                ),
            ]
        )
        session.commit()
        session.close()

        captured = {}

        def fake_curses(stdscr, initial_row, get_prev, get_next, bal_amt):
            rows = [initial_row]
            while True:
                prev = get_prev(rows[0].timestamp)
                if prev is None:
                    break
                prev_row = cli.LedgerRow(
                    prev[0], prev[1], prev[2], rows[0].running - rows[0].amount
                )
                rows.insert(0, prev_row)
                if len(rows) >= 3:
                    break
            captured["rows"] = rows

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2023, 1, 2)

        monkeypatch.setattr(cli, "date", FakeDate)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "ledger_curses", fake_curses)

        cli.ledger_view(object())
        assert [r.description for r in captured["rows"]] == [
            "Rent",
            "Coffee",
            "Lunch",
        ]
    finally:
        path.unlink()


def test_next_event_handles_multiple_recurring():
    """``next_event`` returns each recurring item even on the same day."""
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Recurring(
                    description="Salary",
                    amount=1000.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
                Recurring(
                    description="Rent",
                    amount=-500.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
            ]
        )
        session.commit()
        txns = []
        recs = session.query(Recurring).all()
        ev1 = cli.next_event(datetime(2022, 12, 31), txns, recs)
        ev2 = cli.next_event(ev1[0], txns, recs)
        ev3 = cli.next_event(ev2[0], txns, recs)
        ev4 = cli.next_event(ev3[0], txns, recs)
        assert ev1[0].date() == ev2[0].date() == date(2023, 1, 1)
        assert {ev1[1], ev2[1]} == {"Salary", "Rent"}
        assert ev3[0].date() == ev4[0].date() == date(2023, 2, 1)
        assert {ev3[1], ev4[1]} == {"Salary", "Rent"}
    finally:
        session.close()
        path.unlink()


def test_ledger_view_handles_multiple_recurring(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2022, 12, 31)),
                Recurring(
                    description="Salary",
                    amount=1000.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
                Recurring(
                    description="Rent",
                    amount=-500.0,
                    start_date=datetime(2023, 1, 1),
                    frequency="monthly",
                ),
            ]
        )
        session.commit()
        session.close()

        captured = {}

        def fake_curses(stdscr, initial_row, get_prev, get_next, bal_amt):
            rows = [initial_row]
            prev = get_prev(rows[0].timestamp)
            if prev is not None:
                prev_row = cli.LedgerRow(
                    prev[0], prev[1], prev[2], rows[0].running - rows[0].amount
                )
                rows.insert(0, prev_row)
            while len(rows) < 4:
                nxt = get_next(rows[-1].timestamp)
                if nxt is None:
                    break
                next_row = cli.LedgerRow(
                    nxt[0], nxt[1], nxt[2], rows[-1].running + nxt[2]
                )
                rows.append(next_row)
            captured["rows"] = rows

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2023, 1, 1)

        monkeypatch.setattr(cli, "date", FakeDate)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "ledger_curses", fake_curses)

        cli.ledger_view(object())
        rows = captured["rows"]
        assert rows[0].date == rows[1].date == date(2023, 1, 1)
        assert {rows[0].description, rows[1].description} == {"Salary", "Rent"}
        assert rows[2].date == rows[3].date == date(2023, 2, 1)
        assert {rows[2].description, rows[3].description} == {"Salary", "Rent"}
        running = 0.0
        for r in rows:
            running += r.amount
            assert r.running == running
    finally:
        path.unlink()
