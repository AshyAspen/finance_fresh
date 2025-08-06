"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime, date, timedelta
import curses
import calendar
from dataclasses import dataclass

from .database import SessionLocal, init_db
from .models import Transaction, Balance, Recurring

FREQUENCIES = [
    "weekly",
    "biweekly",
    "semi monthly",
    "quarterly",
    "semi annually",
    "annually",
]


def select(message, choices, default=None):
    """Display a scrollable menu and return the selected value.

    ``choices`` may be a list of strings or ``(title, value)`` pairs. The menu
    is navigated with the arrow keys and the highlighted entry is returned when
    the user presses Enter. ``default`` selects the initially highlighted value
    if provided.
    """

    titles: list[str] = []
    values = []
    default_idx = 0
    for idx, choice in enumerate(choices):
        if isinstance(choice, tuple):
            title, value = choice
        else:
            title = value = choice
        titles.append(title)
        values.append(value)
        if default is not None and value == default:
            default_idx = idx

    selected = scroll_menu(titles, default_idx, header=message)
    return values[selected]


def text(message, default=None):
    """Prompt the user for free-form text input using curses."""

    def _prompt(stdscr):
        curses.curs_set(1)
        stdscr.keypad(True)
        h, w = stdscr.getmaxyx()
        h = max(1, h)
        w = max(1, w)
        prompt = f"{message}" + (f" [{default}]" if default is not None else "") + ": "
        y = h // 2
        x = max(0, (w - len(prompt)) // 2)
        try:
            stdscr.addnstr(y, x, prompt, max(0, w - x))
        except curses.error:
            pass
        stdscr.refresh()
        curses.echo()
        try:
            resp = stdscr.getstr(y, x + len(prompt), max(1, w - x - len(prompt) - 1))
        except curses.error:
            resp = b""
        finally:
            curses.noecho()
        text = resp.decode()
        if text == "" and default is not None:
            return default
        return text

    return curses.wrapper(_prompt)


def transaction_form(
    description: str, timestamp: datetime, amount: float
):
    """Interactive form for editing transaction fields.

    Returns ``(description, timestamp, amount)`` if saved, otherwise ``None``.
    """

    while True:
        choice = select(
            "Select field to edit",
            choices=[
                (f"Name: {description}", "description"),
                (f"Date: {timestamp.strftime('%Y-%m-%d')}", "date"),
                (f"Amount: {amount}", "amount"),
                ("Save", "save"),
                ("Cancel", "cancel"),
            ],
        )

        if choice == "description":
            new_desc = text("Description", default=description)
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = text(
                "Date (YYYY-MM-DD)", default=timestamp.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.")
        elif choice == "amount":
            amount_str = text("Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    print("Invalid amount.")
        elif choice == "save":
            return description, timestamp, amount
        else:
            return None


def add_transaction() -> None:
    """Prompt user for transaction data and persist it."""
    form = transaction_form("", datetime.utcnow(), 0.0)
    if form is None:
        return
    description, timestamp, amount = form
    session = SessionLocal()
    txn = Transaction(description=description, amount=amount, timestamp=timestamp)
    session.add(txn)
    session.commit()
    session.close()
    print("Transaction recorded.\n")


def add_recurring(is_income: bool) -> None:
    """Prompt user for a recurring bill or income and persist it."""

    name = text("Name")
    if name is None:
        return
    date_str = text("Start date (YYYY-MM-DD)")
    if date_str is None:
        return
    amount_str = text("Amount")
    if amount_str is None:
        return
    freq = select("Frequency", FREQUENCIES)
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        amount = float(amount_str)
    except ValueError:
        print("Invalid input.")
        return
    amount = abs(amount) if is_income else -abs(amount)
    session = SessionLocal()
    rec = Recurring(
        description=name, amount=amount, start_date=start, frequency=freq
    )
    session.add(rec)
    session.commit()
    session.close()
    print("Recurring item recorded.\n")


def add_bill() -> None:
    add_recurring(False)


def add_income() -> None:
    add_recurring(True)


def edit_transaction(session, txn: Transaction) -> None:
    """Edit an existing transaction in-place."""
    form = transaction_form(txn.description, txn.timestamp, txn.amount)
    if form is None:
        return
    description, timestamp, amount = form
    txn.description = description
    txn.timestamp = timestamp
    txn.amount = amount
    session.commit()


def list_transactions() -> None:
    """List all transactions in the database and allow editing."""
    session = SessionLocal()
    while True:
        txns = session.query(Transaction).order_by(Transaction.timestamp).all()
        if not txns:
            print("No transactions recorded yet.\n")
            break
        desc_w = max(len(t.description) for t in txns)
        amt_w = max(len(f"{t.amount:.2f}") for t in txns)
        choices = [
            (
                f"{t.timestamp.strftime('%Y-%m-%d')} | {t.description:<{desc_w}} | {t.amount:>{amt_w}.2f}",
                t.id,
            )
            for t in txns
        ]
        choices.append(("Back", None))
        choice = select("Select transaction to edit", choices)
        if choice is None:
            break
        txn = session.get(Transaction, choice)
        if txn is not None:
            edit_transaction(session, txn)
    session.close()


def set_balance() -> None:
    """Prompt the user to store their current balance."""
    amount_str = text("Current balance")
    if amount_str is None:
        return
    try:
        amount = float(amount_str)
    except ValueError:
        print("Invalid amount.")
        return
    session = SessionLocal()
    bal = session.get(Balance, 1)
    if bal is None:
        bal = Balance(id=1, amount=amount)
        session.add(bal)
    else:
        bal.amount = amount
    session.commit()
    session.close()


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_date(d: date, freq: str) -> date:
    if freq == "weekly":
        return d + timedelta(weeks=1)
    if freq == "biweekly":
        return d + timedelta(weeks=2)
    if freq == "semi monthly":
        return d + timedelta(days=15)
    if freq == "quarterly":
        return add_months(d, 3)
    if freq == "semi annually":
        return add_months(d, 6)
    if freq == "annually":
        return add_months(d, 12)
    return d


@dataclass
class LedgerRow:
    date: date
    description: str
    amount: float
    running: float


def ledger_rows(session):
    bal = session.get(Balance, 1)
    running = bal.amount if bal else 0.0
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    recs = session.query(Recurring).all()
    txn_iter = iter(txns)
    next_txn = next(txn_iter, None)
    occurrences = [(r.start_date.date(), r) for r in recs]

    while True:
        next_date = None
        next_kind = None
        if next_txn is not None:
            next_date = next_txn.timestamp.date()
            next_kind = ("txn", next_txn)
        for idx, (occ_date, rec) in enumerate(occurrences):
            if next_date is None or occ_date <= next_date:
                next_date = occ_date
                next_kind = ("rec", idx)
        if next_kind is None:
            break
        if next_kind[0] == "txn":
            running += next_txn.amount
            yield LedgerRow(next_date, next_txn.description, next_txn.amount, running)
            next_txn = next(txn_iter, None)
        else:
            idx = next_kind[1]
            occ_date, rec = occurrences[idx]
            running += rec.amount
            yield LedgerRow(occ_date, rec.description, rec.amount, running)
            occurrences[idx] = (advance_date(occ_date, rec.frequency), rec)


def ledger_curses(initial_rows, row_gen, bal_amt):
    def _view(stdscr):
        rows = list(initial_rows)
        index = 0
        curses.curs_set(0)
        stdscr.keypad(True)

        desc_w = max(len(r.description) for r in rows)
        amt_w = max(len(f"{r.amount:.2f}") for r in rows)
        run_w = max(len(f"{r.running:.2f}") for r in rows)
        footer_left = date.today().isoformat()
        footer_right = f"{bal_amt:.2f}"

        while True:
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            visible = h - 1
            top = min(max(0, index - visible // 2), max(0, len(rows) - visible))

            while len(rows) < top + visible:
                try:
                    row = next(row_gen)
                except StopIteration:
                    break
                rows.append(row)
                desc_w = max(desc_w, len(row.description))
                amt_w = max(amt_w, len(f"{row.amount:.2f}"))
                run_w = max(run_w, len(f"{row.running:.2f}"))

            stdscr.erase()
            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(rows):
                    break
                r = rows[line_idx]
                line = (
                    f"{r.date.strftime('%Y-%m-%d')} | "
                    f"{r.description:<{desc_w}} | "
                    f"{r.amount:>{amt_w}.2f} | {r.running:>{run_w}.2f}"
                )
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addnstr(i, 0, line, w - 1, attr)
                except curses.error:
                    pass

            footer = f"{footer_left} | {footer_right}"
            foot_x = max(0, w - len(footer))
            try:
                stdscr.addnstr(h - 1, foot_x, footer, max(0, w - foot_x))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN:
                index += 1
                if index >= len(rows):
                    try:
                        row = next(row_gen)
                    except StopIteration:
                        index = len(rows) - 1
                    else:
                        rows.append(row)
                        desc_w = max(desc_w, len(row.description))
                        amt_w = max(amt_w, len(f"{row.amount:.2f}"))
                        run_w = max(run_w, len(f"{row.running:.2f}"))
            elif key == ord("q"):
                break

            if index < top:
                top = index
            elif index >= top + visible:
                top = index - visible + 1

    return curses.wrapper(_view)


def scroll_menu(
    entries,
    index,
    height: int | None = None,
    header: str | None = None,
):
    """Display ``entries`` in a curses-driven scrollable window.

    A footer is rendered on the bottom line that shows today's date and the
    stored account balance in the lower-right corner. When ``height`` is not
    provided the list fills the available screen height; otherwise it is limited
    to the given number of rows.
    """

    def _menu(stdscr):
        nonlocal index
        curses.curs_set(0)
        stdscr.keypad(True)

        bal_session = SessionLocal()
        bal = bal_session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        bal_session.close()
        footer_left = date.today().isoformat()
        footer_right = f"{bal_amt:.2f}"

        while True:  # redraw loop
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            offset = 1 if header else 0
            visible = min(len(entries), height or (h - 1 - offset))
            top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

            stdscr.erase()
            if header:
                head_x = max(0, (w - len(header)) // 2)
                try:
                    stdscr.addnstr(0, head_x, header, max(0, w - head_x))
                except curses.error:
                    pass
            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(entries):
                    break
                line = entries[line_idx]
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addstr(i + offset, 0, line[: w - 1], attr)
                except curses.error:
                    pass

            footer = f"{footer_left} | {footer_right}"
            foot_x = max(0, w - len(footer))
            try:
                stdscr.addnstr(h - 1, foot_x, footer, max(0, w - foot_x))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                return index

            if index < top:
                top = index
            elif index >= top + visible:
                top = index - visible + 1

    return curses.wrapper(_menu)


def ledger_view() -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""

    session = SessionLocal()
    bal = session.get(Balance, 1)
    bal_amt = bal.amount if bal else 0.0
    row_gen = ledger_rows(session)
    first = next(row_gen, None)
    if first is None:
        print("No transactions recorded yet.\n")
        session.close()
        return
    session.close()
    ledger_curses([first], row_gen, bal_amt)


def _info_screen(message: str) -> None:
    """Display a simple message screen inside curses."""

    scroll_menu(["Back"], 0, header=message)


def edit_wants_goals() -> None:
    """Placeholder for editing wants/goals."""

    _info_screen("Edit wants/goals (not implemented)")


def toggle_wants_goals() -> None:
    """Placeholder for toggling wants/goals."""

    _info_screen("Toggle wants/goals (not implemented)")


def add_wants_goals() -> None:
    """Placeholder for adding wants/goals."""

    _info_screen("Add wants/goals (not implemented)")


def wants_goals_menu() -> None:
    """Secondary menu for wants/goals related actions."""
    while True:
        choice = select(
            "Wants/Goals options",
            choices=[
                "Edit wants/goals",
                "Toggle wants/goals",
                "Add wants/goals",
                "Back",
            ],
        )
        if choice == "Edit wants/goals":
            edit_wants_goals()
        elif choice == "Toggle wants/goals":
            toggle_wants_goals()
        elif choice == "Add wants/goals":
            add_wants_goals()
        else:
            break


def main() -> None:
    init_db()
    while True:
        choice = select(
            "Select an option",
            choices=[
                "Enter transaction",
                "List transactions",
                "Add bill",
                "Add income",
                "Ledger",
                "Set balance",
                "Wants/Goals",
                "Quit",
            ],
        )
        if choice == "Enter transaction":
            add_transaction()
        elif choice == "List transactions":
            list_transactions()
        elif choice == "Add bill":
            add_bill()
        elif choice == "Add income":
            add_income()
        elif choice == "Ledger":
            ledger_view()
        elif choice == "Set balance":
            set_balance()
        elif choice == "Wants/Goals":
            wants_goals_menu()
        else:
            break


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
