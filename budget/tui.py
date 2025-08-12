import curses
from datetime import datetime, date
from typing import Any, List, Type

from sqlalchemy import inspect

from .database import init_db, SessionLocal
from .models import Recurring, Balance, Goal, IrregularCategory, Transaction

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


def _prompt(stdscr, text: str) -> str:
    h, _ = stdscr.getmaxyx()
    stdscr.move(h - 3, 0)
    stdscr.clrtobot()
    stdscr.addstr(h - 3, 0, text)
    stdscr.refresh()
    curses.echo()
    try:
        result = stdscr.getstr(h - 2, 0).decode().strip()
    finally:
        curses.noecho()
    return result


def _display_table(stdscr, model: ModelType, items: List[Any]) -> None:
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    cols = _get_columns(model)
    widths = {c: len(c) for c in cols}
    for item in items:
        for c in cols:
            widths[c] = max(widths[c], len(_format_value(getattr(item, c))))
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    stdscr.addstr(0, 0, header[:w])
    stdscr.addstr(1, 0, sep[:w])
    for i, item in enumerate(items[: h - 5]):
        row = " | ".join(
            _format_value(getattr(item, c)).ljust(widths[c]) for c in cols
        )
        stdscr.addstr(2 + i, 0, row[:w])
    stdscr.refresh()


def _add_item(stdscr, model: ModelType) -> None:
    cols = _get_columns(model)
    with SessionLocal() as session:
        data = {}
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            val = _prompt(stdscr, f"{c}: ")
            data[c] = _parse_value(col, val)
        obj = model(**data)
        session.add(obj)
        session.commit()


def _edit_item(stdscr, model: ModelType) -> None:
    cols = _get_columns(model)
    item_id = _prompt(stdscr, "id to edit: ")
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if not obj:
            _prompt(stdscr, "not found - press Enter")
            return
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            cur = _format_value(getattr(obj, c))
            val = _prompt(stdscr, f"{c} [{cur}]: ")
            if val:
                setattr(obj, c, _parse_value(col, val))
        session.commit()


def _delete_item(stdscr, model: ModelType) -> None:
    item_id = _prompt(stdscr, "id to delete: ")
    confirm = _prompt(stdscr, "Are you sure? (y/N): ").lower()
    if confirm != "y":
        return
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if obj:
            session.delete(obj)
            session.commit()


def _manage_screen(stdscr, model: ModelType, filters=None) -> None:
    while True:
        with SessionLocal() as session:
            query = session.query(model)
            if filters is not None:
                query = query.filter(filters)
            items = query.order_by(model.id).all()
        _display_table(stdscr, model, items)
        h, _ = stdscr.getmaxyx()
        stdscr.addstr(h - 4, 0, "(a)dd (e)dit (d)elete (q)back")
        stdscr.refresh()
        key = stdscr.getch()
        if key == ord("a"):
            _add_item(stdscr, model)
        elif key == ord("e"):
            _edit_item(stdscr, model)
        elif key == ord("d"):
            _delete_item(stdscr, model)
        elif key == ord("q"):
            break


MENU_ITEMS = [
    ("Incomes", Recurring, Recurring.amount > 0),
    ("Bills", Recurring, Recurring.amount <= 0),
    ("Balance Points", Balance, None),
    ("Irregular Spending", IrregularCategory, None),
    ("Goals", Goal, None),
    ("Transactions", Transaction, None),
    ("Quit", None, None),
]


def _run_menu(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    idx = 0
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Budget Lists")
        for i, (label, _, _) in enumerate(MENU_ITEMS):
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(2 + i, 2, label)
            if i == idx:
                stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(MENU_ITEMS)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(MENU_ITEMS)
        elif key in (curses.KEY_ENTER, 10, 13):
            label, model, filt = MENU_ITEMS[idx]
            if model is None:
                break
            _manage_screen(stdscr, model, filt)
        elif key in (ord("q"), ord("Q")):
            break


def main() -> None:
    init_db()
    curses.wrapper(_run_menu)

