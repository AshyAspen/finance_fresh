"""Database models and access layer for Finance CLI."""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Configure database URL via environment variable for flexibility
DATABASE_URL = os.environ.get("FINANCE_DB", "sqlite:///finance.db")

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class Transaction(Base):
    """Represents a financial transaction."""

    __tablename__ = "transactions"

    id: int = Column(Integer, primary_key=True)
    description: str = Column(String, nullable=False)
    amount: float = Column(Float, nullable=False)
    date: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)


def init_db() -> None:
    """Initialise the database schema."""
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """Create a new database session."""
    return SessionLocal()


def add_transaction(description: str, amount: float, date: datetime | None = None) -> None:
    """Persist a new transaction."""
    session = get_session()
    try:
        txn = Transaction(description=description, amount=amount, date=date or datetime.utcnow())
        session.add(txn)
        session.commit()
    finally:
        session.close()


def get_transactions() -> List[Transaction]:
    """Fetch all transactions ordered by date descending."""
    session = get_session()
    try:
        return session.query(Transaction).order_by(Transaction.date.desc()).all()
    finally:
        session.close()
