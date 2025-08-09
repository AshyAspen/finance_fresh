import itertools
from datetime import datetime, date, timedelta

from tests.helpers import get_temp_session
from budget import cli
from budget.models import (
    Account,
    Transaction,
    Balance,
    Recurring,
    IrregularCategory,
    IrregularState,
)
from budget.services import occurrences_between


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

        def fake_curses(stdscr, initial_row, get_prev, get_next, bal_amt, *args, **kwargs):
            rows = [initial_row]
            while True:
                prev_row = get_prev(rows[0].timestamp)
                if prev_row is None:
                    break
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


def test_ledger_window_overlap():
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
                Transaction(
                    description="Deposit",
                    amount=100.0,
                    timestamp=datetime(2023, 1, 5),
                ),
            ]
        )
        session.commit()
        start = date(2023, 1, 1)
        rows_a = list(cli.ledger_rows(session, start, date(2023, 3, 31)))
        session.close()
        session = Session()
        rows_b = list(cli.ledger_rows(session, start, date(2023, 6, 30)))
        rows_b_trim = [r for r in rows_b if r.date <= date(2023, 3, 31)]
        assert [
            (r.date, r.description, r.amount, r.running) for r in rows_a
        ] == [
            (r.date, r.description, r.amount, r.running) for r in rows_b_trim
        ]
    finally:
        session.close()
        path.unlink()


def test_daily_delta_matches_running_balance():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)),
                Transaction(
                    description="T1",
                    amount=100.0,
                    timestamp=datetime(2023, 1, 1, 10),
                ),
                Transaction(
                    description="T2",
                    amount=-20.0,
                    timestamp=datetime(2023, 1, 1, 12),
                ),
                Recurring(
                    description="Rent",
                    amount=-50.0,
                    start_date=datetime(2023, 1, 2),
                    frequency="weekly",
                ),
            ]
        )
        session.commit()
        rows = list(cli.ledger_rows(session, date(2023, 1, 1), date(2023, 1, 10)))
        by_day: dict[date, list[cli.LedgerRow]] = {}
        for r in rows:
            by_day.setdefault(r.date, []).append(r)
        prev = rows[0].running - rows[0].amount
        for day in sorted(by_day):
            day_rows = by_day[day]
            amounts = sum(r.amount for r in day_rows)
            start_bal = prev
            end_bal = day_rows[-1].running
            assert end_bal - start_bal == amounts
            prev = end_bal
    finally:
        session.close()
        path.unlink()


def test_recurring_occurrence_counts():
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
                Recurring(
                    description="Gym",
                    amount=-20.0,
                    start_date=datetime(2023, 1, 5),
                    frequency="weekly",
                ),
            ]
        )
        session.commit()
        start = date(2023, 1, 1)
        end = date(2023, 2, 28)
        rows = list(cli.ledger_rows(session, start, end))
        counts = {"Rent": 0, "Gym": 0}
        for r in rows:
            if r.description in counts:
                counts[r.description] += 1
        assert counts["Rent"] == len(
            occurrences_between(date(2023, 1, 1), "monthly", start, end)
        )
        assert counts["Gym"] == len(
            occurrences_between(date(2023, 1, 5), "weekly", start, end)
        )
    finally:
        session.close()
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


def test_next_event_monthly_mid_month():
    """Monthly recurring events occurring mid-month are not skipped."""
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add(
            Recurring(
                description="HP Instant Ink",
                amount=-10.0,
                start_date=datetime(2025, 7, 8),
                frequency="monthly",
            )
        )
        session.commit()
        recs = session.query(Recurring).all()
        ev = cli.next_event(datetime(2025, 9, 1), [], recs)
        assert ev[0].date() == date(2025, 9, 8)
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

        def fake_curses(stdscr, initial_row, get_prev, get_next, bal_amt, *args, **kwargs):
            rows = [initial_row]
            prev_row = get_prev(rows[0].timestamp)
            if prev_row is not None:
                rows.insert(0, prev_row)
            while len(rows) < 4:
                next_row = get_next(rows[-1].timestamp)
                if next_row is None:
                    break
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

def test_per_account_offsets_match_stored_balance():
    Session, path = get_temp_session()
    try:
        session = Session()
        a1 = Account(name="A", type="checking")
        a2 = Account(name="B", type="checking")
        session.add_all([a1, a2])
        session.commit()

        bal_a = Balance(amount=100.0, timestamp=datetime(2023, 1, 2), account_id=a1.id)
        bal_b = Balance(amount=200.0, timestamp=datetime(2023, 1, 3), account_id=a2.id)
        session.add_all([
            bal_a,
            bal_b,
            Transaction(
                description="preA",
                amount=-20.0,
                timestamp=datetime(2023, 1, 1),
                account_id=a1.id,
            ),
            Transaction(
                description="markA",
                amount=0.0,
                timestamp=datetime(2023, 1, 2),
                account_id=a1.id,
            ),
            Transaction(
                description="preB",
                amount=50.0,
                timestamp=datetime(2023, 1, 2),
                account_id=a2.id,
            ),
            Transaction(
                description="markB",
                amount=0.0,
                timestamp=datetime(2023, 1, 3),
                account_id=a2.id,
            ),
        ])
        session.commit()

        rows = list(
            cli.ledger_rows(
                session,
                date(2023, 1, 1),
                date(2023, 1, 5),
                [a1.id, a2.id],
            )
        )
        last_by_acct = {}
        bal_ts = {a1.id: date(2023, 1, 2), a2.id: date(2023, 1, 3)}
        for r in rows:
            if r.account_id in bal_ts and r.date <= bal_ts[r.account_id]:
                last_by_acct[r.account_id] = r
        assert abs(last_by_acct[a1.id].running_account - 100.0) < 0.01
        assert abs(last_by_acct[a2.id].running_account - 200.0) < 0.01
    finally:
        session.close()
        path.unlink()


def test_overlap_consistency_multi_account():
    Session, path = get_temp_session()
    try:
        session = Session()
        a1 = Account(name="A", type="checking")
        a2 = Account(name="B", type="checking")
        session.add_all([a1, a2])
        session.commit()
        session.add_all([
            Balance(amount=0.0, timestamp=datetime(2023, 1, 1), account_id=a1.id),
            Balance(amount=0.0, timestamp=datetime(2023, 1, 1), account_id=a2.id),
            Transaction(
                description="A Jan",
                amount=100.0,
                timestamp=datetime(2023, 1, 5),
                account_id=a1.id,
            ),
            Transaction(
                description="A Apr",
                amount=-20.0,
                timestamp=datetime(2023, 4, 5),
                account_id=a1.id,
            ),
            Transaction(
                description="B Feb",
                amount=50.0,
                timestamp=datetime(2023, 2, 5),
                account_id=a2.id,
            ),
            Transaction(
                description="B May",
                amount=-10.0,
                timestamp=datetime(2023, 5, 5),
                account_id=a2.id,
            ),
        ])
        session.commit()
        start = date(2023, 1, 1)
        rows_a = list(
            cli.ledger_rows(session, start, date(2023, 3, 31), [a1.id, a2.id])
        )
        rows_b = list(
            cli.ledger_rows(session, start, date(2023, 6, 30), [a1.id, a2.id])
        )
        rows_b_trim = [r for r in rows_b if r.date <= date(2023, 3, 31)]
        assert [
            (r.timestamp, r.account_id, r.description, r.amount, r.running_account, r.running_total)
            for r in rows_a
        ] == [
            (r.timestamp, r.account_id, r.description, r.amount, r.running_account, r.running_total)
            for r in rows_b_trim
        ]
    finally:
        session.close()
        path.unlink()


def test_irregular_account_scope():
    Session, path = get_temp_session()
    try:
        session = Session()
        a1 = Account(name="A", type="checking")
        a2 = Account(name="B", type="checking")
        session.add_all([a1, a2])
        session.commit()
        today = date.today()
        session.add_all([
            Balance(amount=0.0, timestamp=datetime.combine(today, datetime.min.time()), account_id=a1.id),
            Balance(amount=0.0, timestamp=datetime.combine(today, datetime.min.time()), account_id=a2.id),
            IrregularCategory(
                name="Car",
                account_id=a1.id,
                state=IrregularState(
                    avg_gap_days=30.0,
                    median_amount=100.0,
                    last_event_at=datetime.combine(today - timedelta(days=30), datetime.min.time()),
                ),
            ),
        ])
        session.commit()
        rows = list(
            cli.ledger_rows(
                session,
                today,
                today + timedelta(days=60),
                [a1.id, a2.id],
            )
        )
        irregular_rows = [r for r in rows if r.description == "Irregular"]
        assert irregular_rows, "expected irregular events for account A"
        assert all(r.account_id == a1.id for r in irregular_rows)
    finally:
        session.close()
        path.unlink()
