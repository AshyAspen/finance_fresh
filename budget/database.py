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


def ensure_default_account(session) -> "Account":
    """Ensure a 'Default Checking' account exists and return it."""
    from .models import Account

    acc = session.query(Account).filter_by(name="Default Checking").first()
    if acc:
        return acc

    legacy = session.query(Account).filter_by(name="Default").first()
    if legacy:
        legacy.name = "Default Checking"
        session.commit()
        return legacy

    acc = Account(name="Default Checking", type="checking")
    session.add(acc)
    session.commit()
    return acc

def init_db() -> None:
    """Create database tables if they do not exist."""
    from . import models  # noqa: F401
    insp = inspect(engine)
    required = {
        "accounts",
        "transactions",
        "balance",
        "recurring",
        "goals",
        "irregular_categories",
        "irregular_state",
        "irregular_rules",
    }
    existing = set(insp.get_table_names())
    if not required.issubset(existing):
        Base.metadata.create_all(engine)
        existing = set(insp.get_table_names())

    SessionLocal.configure(bind=engine)
    with SessionLocal() as session:
        default_id = ensure_default_account(session).id

    with engine.begin() as conn:
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
        # If a legacy balance row exists, duplicate it for the default account
        res = conn.execute(
            text("SELECT COUNT(*) FROM balance WHERE account_id = :acc"),
            {"acc": default_id},
        )
        if res.scalar() == 1:
            conn.execute(
                text(
                    "INSERT INTO balance (amount, timestamp, account_id) "
                    "SELECT amount, timestamp, account_id FROM balance WHERE account_id = :acc"
                ),
                {"acc": default_id},
            )

        # Ensure irregular category columns and indexes
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(irregular_categories)"))]
        if "active" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN active BOOLEAN DEFAULT 1"
                )
            )
        if "window_days" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN window_days INTEGER DEFAULT 120"
                )
            )
        if "alpha" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN alpha FLOAT DEFAULT 0.3"
                )
            )
        if "safety_quantile" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN safety_quantile FLOAT DEFAULT 0.8"
                )
            )
        if "account_id" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN account_id INTEGER REFERENCES accounts(id)"
                )
            )
        if "created_at" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN created_at DATETIME"
                )
            )
        if "updated_at" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_categories ADD COLUMN updated_at DATETIME"
                )
            )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_irregular_categories_name ON irregular_categories(name)"
            )
        )

        # Ensure irregular state columns and indexes
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(irregular_state)"))]
        if "category_id" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN category_id INTEGER REFERENCES irregular_categories(id)"
                )
            )
        if "avg_gap_days" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN avg_gap_days FLOAT"
                )
            )
        if "weekday_probs" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN weekday_probs TEXT"
                )
            )
        if "amount_mu" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN amount_mu FLOAT"
                )
            )
        if "amount_sigma" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN amount_sigma FLOAT"
                )
            )
        if "median_amount" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN median_amount FLOAT"
                )
            )
        if "last_event_at" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN last_event_at DATETIME"
                )
            )
        if "updated_at" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_state ADD COLUMN updated_at DATETIME"
                )
            )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_irregular_state_category_id ON irregular_state(category_id)"
            )
        )

        # Ensure irregular rules columns and indexes
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(irregular_rules)"))]
        if "category_id" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_rules ADD COLUMN category_id INTEGER REFERENCES irregular_categories(id)"
                )
            )
        if "pattern" not in cols:
            conn.execute(
                text("ALTER TABLE irregular_rules ADD COLUMN pattern TEXT")
            )
        if "active" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE irregular_rules ADD COLUMN active BOOLEAN DEFAULT 1"
                )
            )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_irregular_rules_category_pattern ON irregular_rules(category_id, pattern)"
            )
        )
