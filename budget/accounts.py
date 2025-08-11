from __future__ import annotations


from sqlalchemy.orm import Session

from .models import Account, Transaction, Recurring, Balance


def list_accounts(session: Session, include_archived: bool = False):
    query = session.query(Account)
    if not include_archived:
        query = query.filter(Account.archived == False)
    return query.order_by(Account.name).all()


def account_has_activity(session: Session, account_id: int) -> bool:
    return (
        session.query(Transaction).filter_by(account_id=account_id).first() is not None
        or session.query(Recurring).filter_by(account_id=account_id).first() is not None
        or session.query(Balance).filter_by(account_id=account_id).first() is not None
    )


def format_account_row(account: Account) -> str:
    row = f"{account.name}  •  {account.type}  •  {account.currency}"
    if getattr(account, "archived", False):
        row += " (archived)"
    return row
