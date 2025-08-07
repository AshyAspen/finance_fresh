from sqlalchemy import create_engine, text

from tests import helpers  # ensures project root on path
from budget import database


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
