from datetime import date, datetime
from typing import Iterable, Literal

from sqlalchemy.orm import Session

from .models import IrregularCategory, IrregularState, IrregularRule


def ensure_category(session: Session, name: str, **kwargs) -> IrregularCategory: ...

def get_or_create_state(session: Session, category_id: int) -> IrregularState: ...

def learn_irregular_state(
    session: Session,
    category_id: int,
    start: date | datetime,
    end: date | datetime,
) -> IrregularState: ...

def forecast_irregular(
    session: Session,
    category_id: int,
    start: date,
    end: date,
    mode: Literal["deterministic", "monte_carlo"] = "deterministic",
    n: int = 500,
): ...

def categories(session: Session) -> list[IrregularCategory]: ...


def rules_for(session: Session, category_id: int) -> list[str]:
    """Return active rule patterns for the given category."""

    rows = (
        session.query(IrregularRule.pattern)
        .filter(
            IrregularRule.category_id == category_id, IrregularRule.active.is_(True)
        )
        .all()
    )
    return [r[0] for r in rows]


def match_category_id(session: Session, description: str) -> int | None:
    """Return the first matching active category for the description."""

    desc = description.lower()
    rules = (
        session.query(IrregularRule)
        .join(IrregularCategory, IrregularRule.category_id == IrregularCategory.id)
        .filter(IrregularRule.active.is_(True), IrregularCategory.active.is_(True))
        .order_by(IrregularRule.id)
        .all()
    )
    for rule in rules:
        if rule.pattern.lower() in desc:
            return rule.category_id
    return None
