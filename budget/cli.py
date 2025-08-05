"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime

import questionary

from .database import SessionLocal, init_db
from .models import Transaction


def add_transaction() -> None:
    """Prompt user for transaction data and persist it."""
    description = questionary.text("Description").ask()
    if description is None:
        return
    amount_str = questionary.text("Amount").ask()
    if amount_str is None:
        return
    try:
        amount = float(amount_str)
    except ValueError:
        print("Invalid amount. Transaction cancelled.")
        return
    session = SessionLocal()
    txn = Transaction(description=description, amount=amount, timestamp=datetime.utcnow())
    session.add(txn)
    session.commit()
    session.close()
    print("Transaction recorded.\n")


def list_transactions() -> None:
    """List all transactions in the database."""
    session = SessionLocal()
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    if not txns:
        print("No transactions recorded yet.\n")
    else:
        for txn in txns:
            ts = txn.timestamp.strftime("%Y-%m-%d %H:%M")
            print(f"{ts} | {txn.description} | ${txn.amount:.2f}")
        print()
    session.close()
    input("Press Enter to continue...")


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
        choice = questionary.select(
            "Wants/Goals options",
            choices=[
                "Edit wants/goals",
                "Toggle wants/goals",
                "Add wants/goals",
                "Back",
            ],
        ).ask()
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
        choice = questionary.select(
            "Select an option",
            choices=[
                "Enter transaction",
                "List transactions",
                "Wants/Goals",
                "Quit",
            ],
        ).ask()
        if choice == "Enter transaction":
            add_transaction()
        elif choice == "List transactions":
            list_transactions()
        elif choice == "Wants/Goals":
            wants_goals_menu()
        else:
            break


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
