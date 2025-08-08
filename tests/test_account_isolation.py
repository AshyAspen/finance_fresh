from tests.helpers import get_temp_session
from budget.models import Account, Transaction


def test_account_isolation():
    Session, path = get_temp_session()
    try:
        session = Session()
        default = session.query(Account).filter_by(name="Default").one()
        secondary = Account(name="Secondary")
        session.add(secondary)
        session.commit()
        session.add_all(
            [
                Transaction(description="D", amount=1.0, account_id=default.id),
                Transaction(description="S", amount=2.0, account_id=secondary.id),
            ]
        )
        session.commit()
        default_cnt = session.query(Transaction).filter_by(account_id=default.id).count()
        secondary_cnt = session.query(Transaction).filter_by(account_id=secondary.id).count()
        assert default_cnt == 1
        assert secondary_cnt == 1
    finally:
        session.close()
        path.unlink()
