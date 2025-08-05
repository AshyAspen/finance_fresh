"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime, date

from simple_term_menu import TerminalMenu

from .database import SessionLocal, init_db
from .models import Transaction, Balance


def select(message, choices, default=None):
    """Display a menu and return the selected value.

    ``choices`` may be a list of strings or ``(title, value)`` pairs.
    """

    titles = []
    values = []
    for choice in choices:
        if isinstance(choice, tuple):
            title, value = choice
        else:
            title = value = choice
        titles.append(title)
        values.append(value)
    index = values.index(default) if default in values else 0
    menu = TerminalMenu(titles, title=message, cursor_index=index, cycle_cursor=True)
    selection = menu.show()
    if selection is None:
        return None
    return values[selection]


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
        choices = [
            (
                f"{t.timestamp.strftime('%Y-%m-%d %H:%M')} | {t.description} | ${t.amount:.2f}",
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


def ledger_view() -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""
    session = SessionLocal()
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    if not txns:
        print("No transactions recorded yet.\n")
        session.close()
        return
    bal = session.get(Balance, 1)
    running = bal.amount if bal else 0.0
    entries = []
    today = date.today()
    today_idx = 0
    for idx, txn in enumerate(txns):
        running += txn.amount
        entry = (
            f"{txn.timestamp.strftime('%Y-%m-%d')} | {txn.description} | {txn.amount:.2f} | {running:.2f}"
        )
        entries.append(entry)
        if txn.timestamp.date() <= today:
            today_idx = idx
    session.close()
    choices = ["Exit"] + entries + ["Exit"]
    default_entry = entries[today_idx] if entries else "Exit"
    while True:
        choice = select("Ledger", choices=choices, default=default_entry)
        if choice == "Exit" or choice is None:
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
