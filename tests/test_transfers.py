from datetime import datetime

from budget.services import create_transfer
from budget.models import Account, Transaction
from tests.helpers import get_temp_session


def test_create_transfer_zero_sum():
    Session, path = get_temp_session()
    try:
        session = Session()
        a1 = Account(name="A", type="checking")
        a2 = Account(name="B", type="checking")
        session.add_all([a1, a2])
        session.commit()

        gid = create_transfer(session, a1.id, a2.id, 50.0, datetime(2023, 1, 1))

        txns = session.query(Transaction).filter(Transaction.transfer_group_id == gid).all()
        assert len(txns) == 2
        assert sum(t.amount for t in txns) == 0.0

        out = next(t for t in txns if t.account_id == a1.id)
        inc = next(t for t in txns if t.account_id == a2.id)
        assert out.amount == -50.0
        assert inc.amount == 50.0
        assert out.counterparty_account_id == a2.id
        assert inc.counterparty_account_id == a1.id
    finally:
        session.close()
        path.unlink()
