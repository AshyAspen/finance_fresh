from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean

from .database import Base


class Transaction(Base):
    """A financial transaction record."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Balance(Base):
    """Stores the user's current balance."""

    __tablename__ = "balance"

    id = Column(Integer, primary_key=True, default=1)
    amount = Column(Float, nullable=False, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Recurring(Base):
    """A recurring bill or income entry."""

    __tablename__ = "recurring"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    frequency = Column(String, nullable=False)


class Goal(Base):
    """A savings goal or want entry."""

    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    target_date = Column(DateTime, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
