import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import database
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
