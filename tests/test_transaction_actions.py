from datetime import datetime

from tests.helpers import make_prompt
from budget import cli
from budget.models import Transaction, Balance

pytest_plugins = ["tests.helpers"]

def test_transaction_persistence(session_factory):
    session = session_factory()
    txn = Transaction(description="Test", amount=10.5)
    session.add(txn)
    session.commit()
    session.close()

    session = session_factory()
    results = session.query(Transaction).all()
    assert len(results) == 1
    assert results[0].description == "Test"
    assert results[0].amount == 10.5
    session.close()


def test_add_transaction_with_date(monkeypatch, session_factory):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(
        cli, "select", make_prompt(["description", "date", "amount", "save"])
    )
    monkeypatch.setattr(
        cli, "text", make_prompt(["Groceries", "2023-02-01", "20.5"])
    )

    cli.add_transaction()

    session = session_factory()
    txns = session.query(Transaction).all()
    assert len(txns) == 1
    txn = txns[0]
    assert txn.description == "Groceries"
    assert txn.amount == 20.5
    assert txn.timestamp.date() == datetime(2023, 2, 1).date()
    session.close()


def test_edit_transaction(monkeypatch, session_factory):
    session = session_factory()
    txn = Transaction(description="Old", amount=5.0, timestamp=datetime(2023, 1, 1))
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
    session.close()


def test_set_balance(monkeypatch, session_factory):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "text", make_prompt(["100.0"]))
    cli.set_balance()
    session = session_factory()
    bal = session.get(Balance, 1)
    assert bal is not None
    assert bal.amount == 100.0
    session.close()


def test_list_transactions_columns(monkeypatch, session_factory):
    session = session_factory()
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

    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    cli.list_transactions()

    titles = captured["entries"]
    assert titles[0] == "2023-01-01 | Short  |  5.00"
    assert titles[1] == "2023-01-02 | Longer | -3.00"
    assert titles[2] == "Back"
    assert captured["kwargs"]["allow_add"] is True
    assert captured["kwargs"]["allow_delete"] is True
    assert captured["kwargs"].get("boxed", False) is False


def test_delete_transaction(monkeypatch, session_factory):
    session = session_factory()
    session.add(
        Transaction(
            description="T",
            amount=5.0,
            timestamp=datetime(2023, 1, 1),
        )
    )
    session.commit()
    session.close()

    responses = iter([("delete", 0), None])

    def fake_scroll(entries, index, **kwargs):
        return next(responses)

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "confirm", lambda msg: True)

    cli.list_transactions()

    session = session_factory()
    assert session.query(Transaction).count() == 0
    session.close()


def test_add_transaction_from_list(monkeypatch, session_factory):
    session = session_factory()
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
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    monkeypatch.setattr(cli, "add_transaction", fake_add)

    cli.list_transactions()

    assert called.get("added") is True
