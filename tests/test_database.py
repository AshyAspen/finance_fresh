import os
import sys
from datetime import datetime

import pytest

# Ensure package root is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure database path for testing before importing database module
@pytest.fixture(autouse=True)
def _set_test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FINANCE_DB", f"sqlite:///{db_path}")
    yield


def test_add_and_get_transactions():
    from finance_cli import database

    database.init_db()
    database.add_transaction("Coffee", 3.50, datetime(2023, 1, 1))

    txns = database.get_transactions()
    assert len(txns) == 1
    assert txns[0].description == "Coffee"
    assert txns[0].amount == 3.50
