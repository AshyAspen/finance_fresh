import os
import sys
import tempfile
from datetime import datetime

# ensure package is importable when running tests directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# set up temporary database before importing budget modules
fd, path = tempfile.mkstemp()
os.close(fd)
os.environ["BUDGET_DB"] = path

from budget.database import init_db, SessionLocal
from budget.models import Transaction, Recurring


def teardown_module(module):
    os.remove(path)
    del os.environ["BUDGET_DB"]


def test_add_transaction():
    init_db()
    with SessionLocal() as session:
        tx = Transaction(description="test", amount=5.0)
        session.add(tx)
        session.commit()
        assert tx.id is not None
        assert session.query(Transaction).count() == 1


def test_add_recurring():
    with SessionLocal() as session:
        r = Recurring(
            description="salary",
            amount=100.0,
            start_date=datetime.utcnow(),
            frequency="monthly",
        )
        session.add(r)
        session.commit()
        assert r.id is not None
        assert session.query(Recurring).count() == 1
