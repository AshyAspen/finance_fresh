"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime

import questionary

from .database import SessionLocal, init_db
from .models import Transaction


def transaction_form(
    description: str, timestamp: datetime, amount: float
):
    """Interactive form for editing transaction fields.

    Returns ``(description, timestamp, amount)`` if saved, otherwise ``None``.
    """

    while True:
        choice = questionary.select(
            "Select field to edit",
            choices=[
                questionary.Choice(title=f"Name: {description}", value="description"),
                questionary.Choice(
                    title=f"Date: {timestamp.strftime('%Y-%m-%d')}", value="date"
                ),
                questionary.Choice(title=f"Amount: {amount}", value="amount"),
                questionary.Choice(title="Save", value="save"),
                questionary.Choice(title="Cancel", value="cancel"),
            ],
        ).ask()

        if choice == "description":
            new_desc = questionary.text("Description", default=description).ask()
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = questionary.text(
                "Date (YYYY-MM-DD)", default=timestamp.strftime("%Y-%m-%d")
            ).ask()
            if date_str is not None:
                try:
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.")
        elif choice == "amount":
            amount_str = questionary.text("Amount", default=str(amount)).ask()
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
            questionary.Choice(
                title=f"{t.timestamp.strftime('%Y-%m-%d %H:%M')} | {t.description} | ${t.amount:.2f}",
                value=t.id,
            )
            for t in txns
        ]
        choices.append(questionary.Choice(title="Back", value=None))
        choice = questionary.select("Select transaction to edit", choices=choices).ask()
        if choice == "Back" or choice is None:
            break
        txn = session.get(Transaction, choice)
        if txn is not None:
            edit_transaction(session, txn)
    session.close()


def main() -> None:
    init_db()
    while True:
        choice = questionary.select(
            "Select an option",
            choices=[
                "Enter transaction",
                "List transactions",
                "Quit",
            ],
        ).ask()
        if choice == "Enter transaction":
            add_transaction()
        elif choice == "List transactions":
            list_transactions()
        else:
            break


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
