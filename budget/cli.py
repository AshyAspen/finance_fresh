from __future__ import annotations

"""Simple text based CLI for managing budget data."""

from datetime import datetime, date
from typing import Any, List, Type

from sqlalchemy import inspect

from .database import SessionLocal, init_db
from .models import (
    Transaction,
    Recurring,
    Balance,
    Goal,
    IrregularCategory,
)

ModelType = Type[Any]


def _get_columns(model: ModelType) -> List[str]:
    mapper = inspect(model)
    return [c.key for c in mapper.columns]


def _parse_value(col, value: str):
    if value == "":
        return None
    pytype = col.type.python_type
    if pytype is float:
        return float(value)
    if pytype is int:
        return int(value)
    if pytype is bool:
        return value.lower() in {"y", "yes", "1", "true"}
    if pytype is datetime:
        return datetime.fromisoformat(value)
    if pytype is date:
        return date.fromisoformat(value)
    return value


def _format_value(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    if v is None:
        return ""
    return str(v)


def _print_table(model: ModelType, items: List[Any]) -> None:
    cols = _get_columns(model)
    widths = {c: len(c) for c in cols}
    for item in items:
        for c in cols:
            widths[c] = max(widths[c], len(_format_value(getattr(item, c))))
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    print(header)
    print(sep)
    for item in items:
        row = " | ".join(
            _format_value(getattr(item, c)).ljust(widths[c]) for c in cols
        )
        print(row)
    if not items:
        print("<empty>")


def _add_item(model: ModelType) -> None:
    cols = _get_columns(model)
    with SessionLocal() as session:
        data = {}
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            val = input(f"{c}: ")
            data[c] = _parse_value(col, val)
        obj = model(**data)
        session.add(obj)
        session.commit()


def _edit_item(model: ModelType) -> None:
    cols = _get_columns(model)
    item_id = input("id to edit: ")
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if not obj:
            print("not found")
            return
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            cur = _format_value(getattr(obj, c))
            val = input(f"{c} [{cur}]: ")
            if val:
                setattr(obj, c, _parse_value(col, val))
        session.commit()


def _delete_item(model: ModelType) -> None:
    item_id = input("id to delete: ")
    confirm = input("Are you sure? (y/N): ").lower()
    if confirm != "y":
        return
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if obj:
            session.delete(obj)
            session.commit()


def _manage(model: ModelType, filters=None) -> None:
    while True:
        with SessionLocal() as session:
            query = session.query(model)
            if filters is not None:
                query = query.filter(filters)
            items = query.order_by(model.id).all()
        _print_table(model, items)
        action = input("(a)dd (e)dit (d)elete (q)uit: ").lower()
        if action == "a":
            _add_item(model)
        elif action == "e":
            _edit_item(model)
        elif action == "d":
            _delete_item(model)
        elif action == "q":
            break


def main() -> None:
    init_db()
    while True:
        print("\nBudget Lists")
        print("1. Incomes")
        print("2. Bills")
        print("3. Balance Points")
        print("4. Irregular Spending")
        print("5. Goals")
        print("6. Transactions")
        print("q. Quit")
        choice = input("Select option: ").lower()
        if choice == "1":
            _manage(Recurring, Recurring.amount > 0)
        elif choice == "2":
            _manage(Recurring, Recurring.amount <= 0)
        elif choice == "3":
            _manage(Balance)
        elif choice == "4":
            _manage(IrregularCategory)
        elif choice == "5":
            _manage(Goal)
        elif choice == "6":
            _manage(Transaction)
        elif choice == "q":
            break


if __name__ == "__main__":
    main()
