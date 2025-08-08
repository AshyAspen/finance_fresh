from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
)

from .database import Base


class Account(Base):
    """Bank or cash account used for financial records."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    type = Column(String, nullable=False, default="checking")
    institution = Column(String)
    last4 = Column(String(8))
    currency = Column(String(3), default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)
    archived = Column(Boolean, default=False)


class Transaction(Base):
    """A financial transaction record."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        index=True,
        nullable=False,
        default=1,
    )


class Balance(Base):
    """Stores the user's current balance."""

    __tablename__ = "balance"

    id = Column(Integer, primary_key=True, default=1)
    amount = Column(Float, nullable=False, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        index=True,
        nullable=False,
        default=1,
    )


class Recurring(Base):
    """A recurring bill or income entry."""

    __tablename__ = "recurring"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    frequency = Column(String, nullable=False)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        index=True,
        nullable=False,
        default=1,
    )


class Goal(Base):
    """A savings goal or want entry."""

    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    target_date = Column(DateTime, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        index=True,
        nullable=False,
        default=1,
    )
