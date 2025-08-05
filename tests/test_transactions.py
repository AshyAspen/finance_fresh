import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import cli, database
from budget.models import Transaction, Balance


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


def test_ledger_running_balance(monkeypatch):
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
        session.close()

        captured = {}

        def fake_scroll(entries, index, height=10, header=None):
            captured["entries"] = entries
            captured["index"] = index
            captured["header"] = header
            # return bottom "Exit" to exit immediately
            return len(entries) - 1

        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        cli.ledger_view()

        titles = captured["entries"]
        assert titles[0] == "Exit"
        assert titles[1] == "2023-01-01 | T1 | -10.00 |  90.00"
        assert titles[2] == "2023-01-02 | T2 |  20.00 | 110.00"
        assert titles[3] == "Exit"
        assert captured["index"] == 2
        assert captured["header"] is None
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

    def fake_scroll(entries, index, height=10, header=None):
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
