import os
from pathlib import Path
from sqlalchemy import create_engine
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
    Base.metadata.create_all(engine)
