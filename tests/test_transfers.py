from datetime import datetime, date

from budget.services import create_transfer, delete_transfer, update_transfer
from budget.models import Account, Transaction, Balance
from budget import cli
from tests.helpers import get_temp_session


def test_transfers_zero_sum():
    Session, path = get_temp_session()
    try:
        session = Session()
        a1 = Account(name="A", type="checking")
        a2 = Account(name="B", type="checking")
        session.add_all([a1, a2])
        session.commit()
        session.add_all([
            Balance(amount=0.0, timestamp=datetime(2023, 1, 1), account_id=a1.id),
            Balance(amount=0.0, timestamp=datetime(2023, 1, 1), account_id=a2.id),
        ])
        session.commit()

        tid = create_transfer(session, a1.id, a2.id, 50.0, datetime(2023, 1, 1))

        txns = session.query(Transaction).filter(Transaction.transfer_id == tid).all()
        assert len(txns) == 2
        assert sum(t.amount for t in txns) == 0.0

        out = next(t for t in txns if t.account_id == a1.id)
        inc = next(t for t in txns if t.account_id == a2.id)
        assert out.amount == -50.0
        assert inc.amount == 50.0

        update_transfer(session, tid, "Xfer", 60.0, datetime(2023, 1, 2))
        txns = session.query(Transaction).filter(Transaction.transfer_id == tid).all()
        assert sorted(t.amount for t in txns) == [-60.0, 60.0]

        rows_a = list(
            cli.ledger_rows(session, date(2023, 1, 1), date(2023, 1, 2), [a1.id])
        )
        rows_b = list(
            cli.ledger_rows(session, date(2023, 1, 1), date(2023, 1, 2), [a2.id])
        )
        rows_ab = list(
            cli.ledger_rows(session, date(2023, 1, 1), date(2023, 1, 2), [a1.id, a2.id])
        )
        assert [(r.account_id, r.amount) for r in rows_a] == [(a1.id, -60.0)]
        assert [(r.account_id, r.amount) for r in rows_b] == [(a2.id, 60.0)]
        assert sorted((r.account_id, r.amount) for r in rows_ab) == [
            (a1.id, -60.0),
            (a2.id, 60.0),
        ]

        delete_transfer(session, tid)
        assert (
            session.query(Transaction).filter(Transaction.transfer_id == tid).count() == 0
        )
    finally:
        session.close()
        path.unlink()
