from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.orm import relationship

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
    """Stores balance snapshots per account."""

    __tablename__ = "balance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    amount = Column(Float, nullable=False, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        index=True,
        nullable=False,
        default=1,
    )

    __table_args__ = (
        Index("ix_balance_account_id_timestamp", "account_id", "timestamp"),
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


class IrregularCategory(Base):
    """Category tracking irregular transactions (e.g. car repairs)."""

    __tablename__ = "irregular_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    active = Column(Boolean, default=True)
    window_days = Column(Integer, default=120)
    alpha = Column(Float, default=0.3)
    safety_quantile = Column(Float, default=0.8)
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
        default=1,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    state = relationship(
        "IrregularState",
        uselist=False,
        back_populates="category",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_irregular_categories_name", "name"),)

    def __init__(self, **kwargs):
        if "state" not in kwargs:
            kwargs["state"] = IrregularState()
        super().__init__(**kwargs)


class IrregularState(Base):
    """Learned state about an irregular category."""

    __tablename__ = "irregular_state"

    id = Column(Integer, primary_key=True)
    category_id = Column(
        Integer,
        ForeignKey("irregular_categories.id"),
        nullable=False,
    )
    avg_gap_days = Column(Float)
    weekday_probs = Column(Text)
    amount_mu = Column(Float)
    amount_sigma = Column(Float)
    median_amount = Column(Float)
    last_event_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("IrregularCategory", back_populates="state")

    __table_args__ = (
        Index("ix_irregular_state_category_id", "category_id", unique=True),
    )


class IrregularRule(Base):
    """Simple substring rule for mapping transactions to irregular categories."""

    __tablename__ = "irregular_rules"

    id = Column(Integer, primary_key=True)
    category_id = Column(
        Integer, ForeignKey("irregular_categories.id"), nullable=False, index=True
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
        default=1,
    )
    pattern = Column(String, nullable=False)
    active = Column(Boolean, default=True)

    category = relationship("IrregularCategory", backref="rules")

    __table_args__ = (
        Index("ix_irregular_rules_category_pattern", "category_id", "pattern"),
    )
