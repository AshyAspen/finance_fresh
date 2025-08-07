import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, date
import itertools

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import cli, database
from budget.models import Transaction, Balance, Recurring
import pytest


def get_temp_session():
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    TestingSession = sessionmaker(bind=engine)
    database.Base.metadata.create_all(engine)
    return TestingSession, Path(db_path)


def test_transaction_persistence():
    Session, path = get_temp_session()
    try:
        session = Session()
        txn = Transaction(description="Test", amount=10.5)
        session.add(txn)
        session.commit()
        session.close()

        session = Session()
        results = session.query(Transaction).all()
        assert len(results) == 1
        assert results[0].description == "Test"
        assert results[0].amount == 10.5
    finally:
        path.unlink()


def make_prompt(responses):
    iterator = iter(responses)

    def _prompt(*args, **kwargs):
        return next(iterator)

    return _prompt


def test_add_transaction_with_date(monkeypatch):
    Session, path = get_temp_session()
    try:
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(
            cli, "select", make_prompt(["description", "date", "amount", "save"])
        )
        monkeypatch.setattr(
            cli, "text", make_prompt(["Groceries", "2023-02-01", "20.5"])
        )

        cli.add_transaction()

        session = Session()
        txns = session.query(Transaction).all()
        assert len(txns) == 1
        txn = txns[0]
        assert txn.description == "Groceries"
        assert txn.amount == 20.5
        assert txn.timestamp.date() == datetime(2023, 2, 1).date()
    finally:
        session.close()
        path.unlink()


def test_edit_transaction(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        txn = Transaction(
            description="Old", amount=5.0, timestamp=datetime(2023, 1, 1)
        )
        session.add(txn)
        session.commit()

        monkeypatch.setattr(
            cli, "select", make_prompt(["description", "amount", "date", "save"])
        )
        monkeypatch.setattr(
            cli, "text", make_prompt(["New", "10.0", "2023-03-03"])
        )

        cli.edit_transaction(session, txn)
        session.refresh(txn)
        assert txn.description == "New"
        assert txn.amount == 10.0
        assert txn.timestamp.date() == datetime(2023, 3, 3).date()
    finally:
        session.close()
        path.unlink()


def test_set_balance(monkeypatch):
    Session, path = get_temp_session()
    try:
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "text", make_prompt(["100.0"]))
        cli.set_balance()
        session = Session()
        bal = session.get(Balance, 1)
        assert bal is not None
        assert bal.amount == 100.0
    finally:
        session.close()
        path.unlink()


def test_init_db_adds_balance_timestamp(tmp_path, monkeypatch):
    # create legacy database lacking timestamp column
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE balance (id INTEGER PRIMARY KEY, amount FLOAT NOT NULL DEFAULT 0.0)"))

    # patch database engine to use legacy DB and run init_db
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()

    # verify timestamp column now exists
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(balance)"))]
        assert "timestamp" in cols


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
        assert [r.description for r in rows] == [
            "Rent",
            "Coffee",
            "Lunch",
        ]
        assert [r.running for r in rows] == [-50.0, -55.0, -65.0]
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

        def fake_curses(initial_row, get_prev, get_next, bal_amt):
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

        cli.ledger_view()
        assert [r.description for r in captured["rows"]] == [
            "Rent",
            "Coffee",
            "Lunch",
        ]
    finally:
        path.unlink()


def test_list_transactions_columns(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Transaction(description="Short", amount=5.0, timestamp=datetime(2023, 1, 1)),
                Transaction(description="Longer", amount=-3.0, timestamp=datetime(2023, 1, 2)),
            ]
        )
        session.commit()
        session.close()

        captured = {}

        def fake_scroll(entries, index, **kwargs):
            captured["entries"] = entries
            captured["kwargs"] = kwargs
            return None

        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

        cli.list_transactions()

        titles = captured["entries"]
        assert titles[0] == "2023-01-01 | Short  |  5.00"
        assert titles[1] == "2023-01-02 | Longer | -3.00"
        assert titles[2] == "Back"
        assert captured["kwargs"]["allow_add"] is True
        assert captured["kwargs"]["allow_delete"] is True
        assert captured["kwargs"].get("boxed", False) is False
    finally:
        path.unlink()


def test_select_uses_scroll_menu(monkeypatch):
    captured = {}

    def fake_scroll(entries, index, header=None, **kwargs):
        captured["entries"] = entries
        captured["index"] = index
        captured["header"] = header
        captured["boxed"] = kwargs.get("boxed")
        return 1  # choose second item

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())

    result = cli.select("Pick", ["A", ("B title", "b"), "C"], default="b")

    assert captured["entries"] == ["A", "B title", "C"]
    assert captured["index"] == 1
    assert captured["header"] == "Pick"
    assert captured["boxed"] is True
    assert result == "b"


def test_text_prompt_curses(monkeypatch):
    responses = [b"hello", b""]

    def fake_wrapper(func):
        class FakeStdScr:
            def getmaxyx(self):
                return (24, 80)

            def keypad(self, flag):
                pass

        return func(FakeStdScr())

    def fake_newwin(h, w, y, x):
        class FakeWin:
            def box(self):
                pass

            def addnstr(self, *args, **kwargs):
                pass

            def refresh(self):
                pass

            def getstr(self, y, x, n):
                return responses.pop(0)

        return FakeWin()

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "echo", lambda: None)
    monkeypatch.setattr(cli.curses, "noecho", lambda: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)

    assert cli.text("Prompt") == "hello"
    assert cli.text("Prompt", default="dflt") == "dflt"


def test_confirm_prompt_curses(monkeypatch):
    keys = [10, ord("x")]

    def fake_wrapper(func):
        class FakeStdScr:
            def getmaxyx(self):
                return (24, 80)

            def keypad(self, flag):
                pass

        return func(FakeStdScr())

    def fake_newwin(h, w, y, x):
        class FakeWin:
            def box(self):
                pass

            def addnstr(self, *args, **kwargs):
                pass

            def refresh(self):
                pass

            def getch(self):
                return keys.pop(0)

        return FakeWin()

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)

    assert cli.confirm("Sure?") is True
    assert cli.confirm("Sure?") is False


def test_scroll_menu_handles_curses_error(monkeypatch):
    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

    def fake_wrapper(func):
        class FakeWin:
            def getmaxyx(self):
                return (0, 0)

            def addnstr(self, *args, **kwargs):
                raise cli.curses.error

            def addstr(self, *args, **kwargs):
                raise cli.curses.error

            def erase(self):
                pass

            def refresh(self):
                pass

            def keypad(self, flag):
                pass

            def getch(self):
                return 10  # Enter to select

            def box(self):
                pass

        return func(FakeWin())

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", lambda *args, **kwargs: fake_wrapper(lambda w: w))

    index = cli.scroll_menu(["A", "B"], 0, header="hdr")
    assert index == 0


def test_scroll_menu_quits_on_q(monkeypatch):
    def fake_wrapper(func):
        class FakeWin:
            def getmaxyx(self):
                return (24, 80)

            def addnstr(self, *args, **kwargs):
                pass

            def addstr(self, *args, **kwargs):
                pass

            def erase(self):
                pass

            def refresh(self):
                pass

            def keypad(self, flag):
                pass

            def getch(self):
                return ord("q")

            def box(self):
                pass

        return func(FakeWin())

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", lambda *args, **kwargs: fake_wrapper(lambda w: w))

    index = cli.scroll_menu(["A", "B"], 0)
    assert index is None


def test_select_returns_none_on_quit(monkeypatch):
    def fake_scroll(entries, index, header=None, **kwargs):
        return None

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())

    result = cli.select("Pick", ["A", "B"])
    assert result is None


@pytest.mark.parametrize("is_income, amount", [(False, -10.0), (True, 10.0)])
def test_delete_recurring(monkeypatch, is_income, amount):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add(
            Recurring(
                description="R",
                amount=amount,
                start_date=datetime(2023, 1, 1),
                frequency="monthly",
            )
        )
        session.commit()
        session.close()

        responses = [("delete", 0), None]

        def fake_scroll(entries, index, **kwargs):
            return responses.pop(0)

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "confirm", lambda msg: True)

        cli.edit_recurring(is_income)

        session = Session()
        assert session.query(Recurring).count() == 0
    finally:
        session.close()
        path.unlink()


def test_delete_transaction(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add(
            Transaction(
                description="T",
                amount=5.0,
                timestamp=datetime(2023, 1, 1),
            )
        )
        session.commit()
        session.close()

        def fake_scroll(entries, index, **kwargs):
            return ("delete", 0)

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "confirm", lambda msg: True)

        cli.list_transactions()

        session = Session()
        assert session.query(Transaction).count() == 0
    finally:
        session.close()
        path.unlink()


def test_add_transaction_from_list(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add(
            Transaction(
                description="T",
                amount=5.0,
                timestamp=datetime(2023, 1, 1),
            )
        )
        session.commit()
        session.close()

        responses = iter([-1, None])

        def fake_scroll(entries, index, **kwargs):
            return next(responses)

        called = {}

        def fake_add():
            called["added"] = True

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "add_transaction", fake_add)

        cli.list_transactions()

        assert called.get("added") is True
    finally:
        path.unlink()


def test_main_menu_not_boxed(monkeypatch):
    captured = {}

    def fake_select(message, choices, default=None, boxed=True):
        captured["boxed"] = boxed
        return "Quit"

    monkeypatch.setattr(cli, "select", fake_select)
    monkeypatch.setattr(cli, "init_db", lambda: None)

    cli.main()

    assert captured.get("boxed") is False
