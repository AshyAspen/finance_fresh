from datetime import date, datetime
from typing import Iterable, Literal

from sqlalchemy.orm import Session

from .models import IrregularCategory, IrregularState


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
