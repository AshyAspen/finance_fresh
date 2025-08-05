from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime

from .database import Base


class Transaction(Base):
    """A financial transaction record."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
