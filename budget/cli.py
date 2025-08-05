"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime, date
import curses

from .database import SessionLocal, init_db
from .models import Transaction, Balance


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
    """Prompt the user for free-form text input."""

    prompt = f"{message}" + (f" [{default}]" if default is not None else "") + ": "
    response = input(prompt)
    if response == "" and default is not None:
        return default
    return response


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


def build_ledger_entries():
    """Return formatted ledger entries and default index.

    The returned list includes "Exit" at the beginning and end. The default
    index highlights the most recent past transaction relative to today.
    """

    session = SessionLocal()
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    if not txns:
        session.close()
        return [], 0
    bal = session.get(Balance, 1)
    base = bal.amount if bal else 0.0
    running_values = []
    running = base
    for txn in txns:
        running += txn.amount
        running_values.append(running)
    desc_w = max(len(t.description) for t in txns)
    amt_w = max(len(f"{t.amount:.2f}") for t in txns)
    run_w = max(len(f"{r:.2f}") for r in running_values)
    entries = []
    today = date.today()
    today_idx = 0
    running = base
    for idx, txn in enumerate(txns):
        running += txn.amount
        date_str = txn.timestamp.strftime("%Y-%m-%d")
        desc = f"{txn.description:<{desc_w}}"
        amt = f"{txn.amount:>{amt_w}.2f}"
        run = f"{running:>{run_w}.2f}"
        entry = f"{date_str} | {desc} | {amt} | {run}"
        entries.append(entry)
        if txn.timestamp.date() <= today:
            today_idx = idx
    session.close()
    choices = ["Exit"] + entries + ["Exit"]
    default_idx = today_idx + 1  # account for leading Exit
    return choices, default_idx


def scroll_menu(entries, index, header: str | None = None):
    """Display ``entries`` in a curses-driven scrollable window.

    The entire screen (minus a bottom status bar) is used for the list. The
    bottom bar shows today's date on the left and the stored balance on the
    right. Navigation is performed with the arrow keys and Enter returns the
    index of the highlighted entry.
    """

    def _menu(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)

        bal_session = SessionLocal()
        bal = bal_session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        bal_session.close()

        h, w = stdscr.getmaxyx()
        list_h = h - 1 - (1 if header else 0)
        top = max(0, min(len(entries) - list_h, index - list_h // 2))

        while True:
            h, w = stdscr.getmaxyx()
            list_h = h - 1 - (1 if header else 0)
            stdscr.erase()
            if header:
                stdscr.addnstr(0, 0, header, w - 1)
                start_y = 1
            else:
                start_y = 0

            for i in range(list_h):
                line_idx = top + i
                if line_idx >= len(entries):
                    break
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                stdscr.addnstr(start_y + i, 0, entries[line_idx], w - 1, attr)

            date_str = date.today().isoformat()
            bal_str = f"{bal_amt:.2f}"
            stdscr.addnstr(h - 1, 0, date_str, w - 1)
            stdscr.addnstr(h - 1, max(0, w - len(bal_str)), bal_str, len(bal_str))
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
            elif index >= top + list_h:
                top = index - list_h + 1

    return curses.wrapper(_menu)


def ledger_view() -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""

    entries, index = build_ledger_entries()
    if not entries:
        print("No transactions recorded yet.\n")
        return
    while True:
        index = scroll_menu(entries, index)
        if entries[index] == "Exit":
            break


def edit_wants_goals() -> None:
    """Placeholder for editing wants/goals."""
    print("Edit wants/goals selected (feature not implemented).\n")
    input("Press Enter to continue...")


def toggle_wants_goals() -> None:
    """Placeholder for toggling wants/goals."""
    print("Toggle wants/goals selected (feature not implemented).\n")
    input("Press Enter to continue...")


def add_wants_goals() -> None:
    """Placeholder for adding wants/goals."""
    print("Add wants/goals selected (feature not implemented).\n")
    input("Press Enter to continue...")


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
