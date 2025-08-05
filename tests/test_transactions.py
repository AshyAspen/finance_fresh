import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import questionary

from budget import cli, database
from budget.models import Transaction


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
        class Prompt:
            def ask(self):
                return next(iterator)

        return Prompt()

    return _prompt


def test_add_transaction_with_date(monkeypatch):
    Session, path = get_temp_session()
    try:
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(
            questionary, "select", make_prompt(["description", "date", "amount", "save"])
        )
        monkeypatch.setattr(
            questionary, "text", make_prompt(["Groceries", "2023-02-01", "20.5"])
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
            questionary, "select", make_prompt(["description", "amount", "date", "save"])
        )
        monkeypatch.setattr(
            questionary, "text", make_prompt(["New", "10.0", "2023-03-03"])
        )

        cli.edit_transaction(session, txn)
        session.refresh(txn)
        assert txn.description == "New"
        assert txn.amount == 10.0
        assert txn.timestamp.date() == datetime(2023, 3, 3).date()
    finally:
        session.close()
        path.unlink()
