"""Command line interface for Finance CLI."""
from __future__ import annotations

from datetime import datetime

import questionary

from .database import add_transaction, get_transactions, init_db


def main() -> None:
    """Entry point for the Finance CLI."""
    init_db()
    while True:
        choice = questionary.select(
            "Choose an option:",
            choices=["Enter transaction", "List transactions", "Quit"],
        ).ask()

        if choice == "Enter transaction":
            description = questionary.text("Description:").ask()
            amount_str = questionary.text("Amount:").ask()
            try:
                amount = float(amount_str)
            except ValueError:
                print("Invalid amount. Please enter a numeric value.")
                continue
            add_transaction(description=description, amount=amount, date=datetime.utcnow())
            print("Transaction saved.\n")
        elif choice == "List transactions":
            txns = get_transactions()
            if not txns:
                print("No transactions recorded.\n")
            else:
                for txn in txns:
                    print(f"{txn.date:%Y-%m-%d %H:%M} - {txn.description}: ${txn.amount:.2f}")
                print()
            questionary.press_any_key("Press any key to return to menu").ask()
        else:
            break


if __name__ == "__main__":
    main()
