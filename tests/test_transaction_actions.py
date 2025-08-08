from datetime import datetime

from tests.helpers import get_temp_session, make_prompt
from budget import cli
from budget.models import (
    Transaction,
    Balance,
    IrregularCategory,
    IrregularRule,
    IrregularState,
)


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

        cli.add_transaction(object())

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

        cli.edit_transaction(object(), session, txn)
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
        cli.set_balance(object())
        session = Session()
        bal = session.get(Balance, 1)
        assert bal is not None
        assert bal.amount == 100.0
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

        def fake_scroll(stdscr, entries, index, **kwargs):
            captured["entries"] = entries
            captured["kwargs"] = kwargs
            return None

        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

        cli.list_transactions(object())

        titles = captured["entries"]
        assert titles[0] == "2023-01-01 | Short  |  5.00"
        assert titles[1] == "2023-01-02 | Longer | -3.00"
        assert titles[2] == "Back"
        assert captured["kwargs"]["allow_add"] is True
        assert captured["kwargs"]["allow_delete"] is True
        assert captured["kwargs"].get("boxed", False) is False
    finally:
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

        responses = iter([("delete", 0), None])

        def fake_scroll(stdscr, entries, index, **kwargs):
            return next(responses)

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "confirm", lambda stdscr, msg: True)

        cli.list_transactions(object())

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

        def fake_scroll(stdscr, entries, index, **kwargs):
            return next(responses)

        called = {}

        def fake_add(stdscr):
            called["added"] = True

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "add_transaction", fake_add)

        cli.list_transactions(object())

        assert called.get("added") is True
    finally:
        path.unlink()


def test_add_transaction_updates_irregular_state(monkeypatch):
    Session, path = get_temp_session()
    try:
        # Seed category and rule that will match the new transaction
        session = Session()
        cat = IrregularCategory(name="Groceries")
        session.add(cat)
        session.commit()
        cat_id = cat.id
        session.add(IrregularRule(category_id=cat_id, pattern="grocer"))
        session.commit()
        session.close()

        # Bypass interactive form and capture toast
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(
            cli,
            "transaction_form",
            lambda stdscr, d, t, a: ("Local Grocer", datetime(2023, 4, 2), -20.0),
        )
        captured = {}

        def fake_toast(stdscr, msg, ms=900):
            captured["msg"] = msg

        monkeypatch.setattr(cli, "toast", fake_toast)

        cli.add_transaction(object())

        session = Session()
        state = session.query(IrregularState).filter_by(category_id=cat_id).one()
        assert state.last_event_at.date() == datetime(2023, 4, 2).date()
        assert state.median_amount == 20.0
        assert "Updated \u2018Groceries\u2019" in captured.get("msg", "")
    finally:
        session.close()
        path.unlink()
