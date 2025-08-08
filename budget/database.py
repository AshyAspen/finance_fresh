import os
from pathlib import Path
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, declarative_base

# Determine database path; allow override with environment variable for testing
DB_FILE = os.getenv("BUDGET_DB", None)
if DB_FILE is None:
    DB_FILE = Path(__file__).resolve().parent / "transactions.db"
else:
    DB_FILE = Path(DB_FILE)

engine = create_engine(f"sqlite:///{DB_FILE}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()

def init_db() -> None:
    """Create database tables if they do not exist."""
    from . import models  # noqa: F401
    insp = inspect(engine)
    required = {"accounts", "transactions", "balance", "recurring", "goals"}
    existing = set(insp.get_table_names())
    if not required.issubset(existing):
        Base.metadata.create_all(engine)
        existing = set(insp.get_table_names())

    with engine.begin() as conn:
        # Ensure default account exists and capture its id
        res = conn.execute(text("SELECT id FROM accounts WHERE name='Default'"))
        row = res.fetchone()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO accounts (id, name, type) VALUES (1, 'Default', 'checking')"
                )
            )
            default_id = 1
        else:
            default_id = row[0]

        # Ensure account_id columns exist and backfill
        for table in ["transactions", "balance", "recurring", "goals"]:
            cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
            if table == "balance" and "timestamp" not in cols:
                conn.execute(text("ALTER TABLE balance ADD COLUMN timestamp DATETIME"))
                conn.execute(
                    text(
                        "UPDATE balance SET timestamp = CURRENT_TIMESTAMP WHERE timestamp IS NULL"
                    )
                )
                cols.append("timestamp")

            if "account_id" not in cols:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN account_id INTEGER DEFAULT {default_id}"
                    )
                )
            conn.execute(
                text(
                    f"UPDATE {table} SET account_id = {default_id} WHERE account_id IS NULL"
                )
            )
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_account_id ON {table}(account_id)"
                )
            )
            if table in {"transactions", "balance"} and "timestamp" in cols:
                conn.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_account_id_timestamp ON {table}(account_id, timestamp)"
                    )
                )
