from datetime import date, datetime, timedelta

from budget.models import Balance, Recurring
from budget.services import max_safe_payment_today, simulate_balances
from tests.helpers import get_temp_session


def test_max_safe_payment_today():
    Session, _ = get_temp_session()
    session = Session()

    today = date.today()

    # starting balance
    session.add(
        Balance(
            amount=1000.0,
            timestamp=datetime.combine(today, datetime.min.time()),
            account_id=1,
        )
    )

    # upcoming expense in 30 days
    session.add(
        Recurring(
            description="Rent",
            amount=-400.0,
            start_date=datetime.combine(today + timedelta(days=30), datetime.min.time()),
            frequency="monthly",
            account_id=1,
        )
    )

    session.commit()

    buffers = {1: 100.0}

    safe = max_safe_payment_today(session, 1, buffers, horizon_days=59)
    assert abs(safe - 500.0) < 0.02

    # safe amount keeps balance above buffer
    balances_safe = simulate_balances(
        session,
        today,
        today + timedelta(days=59),
        [1],
        {1: -safe},
    )
    assert all(b >= 100.0 for _, b in balances_safe[1])

    # paying one more dollar violates buffer
    balances_too_much = simulate_balances(
        session,
        today,
        today + timedelta(days=59),
        [1],
        {1: -(safe + 1)},
    )
    assert any(b < 100.0 for _, b in balances_too_much[1])

    session.close()

