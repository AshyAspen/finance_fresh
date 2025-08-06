import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime
import itertools

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import cli, database
from budget.models import Transaction, Balance, Recurring


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


def test_ledger_running_balance():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=100.0),
                Transaction(description="T1", amount=-10.0, timestamp=datetime(2023, 1, 1)),
                Transaction(description="T2", amount=20.0, timestamp=datetime(2023, 1, 2)),
            ]
        )
        session.commit()
        rows = list(cli.ledger_rows(session))
        assert rows[0].running == 90.0
        assert rows[1].running == 110.0
    finally:
        session.close()
        path.unlink()


def test_ledger_includes_recurring():
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Balance(id=1, amount=0.0),
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

        def fake_select(message, choices, default=None):
            captured["choices"] = choices
            return None

        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "select", fake_select)

        cli.list_transactions()

        titles = [title for title, _ in captured["choices"]]
        assert titles[0] == "2023-01-01 | Short  |  5.00"
        assert titles[1] == "2023-01-02 | Longer | -3.00"
        assert titles[2] == "Back"
    finally:
        path.unlink()


def test_select_uses_scroll_menu(monkeypatch):
    captured = {}

    def fake_scroll(entries, index, header=None):
        captured["entries"] = entries
        captured["index"] = index
        captured["header"] = header
        return 1  # choose second item

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    result = cli.select(
        "Pick", ["A", ("B title", "b"), "C"], default="b"
    )

    assert captured["entries"] == ["A", "B title", "C"]
    assert captured["index"] == 1
    assert captured["header"] == "Pick"
    assert result == "b"


def test_text_prompt_curses(monkeypatch):
    responses = [b"hello", b""]

    def fake_wrapper(func):
        class FakeWin:
            def __init__(self, resp):
                self.resp = resp

            def getmaxyx(self):
                return (24, 80)

            def addnstr(self, *args, **kwargs):
                pass

            def addstr(self, *args, **kwargs):
                pass

            def refresh(self):
                pass

            def keypad(self, flag):
                pass

            def getstr(self, y, x, n):
                return self.resp

        return func(FakeWin(responses.pop(0)))

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "echo", lambda: None)
    monkeypatch.setattr(cli.curses, "noecho", lambda: None)

    assert cli.text("Prompt") == "hello"
    assert cli.text("Prompt", default="dflt") == "dflt"


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

        return func(FakeWin())

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)

    index = cli.scroll_menu(["A", "B"], 0, header="hdr")
    assert index == 0
