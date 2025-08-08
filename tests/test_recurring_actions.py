from datetime import datetime

import pytest

from tests.helpers import get_temp_session
from budget import cli
from budget.models import Recurring


@pytest.mark.parametrize("is_income, amount", [(False, -10.0), (True, 10.0)])
def test_delete_recurring(monkeypatch, is_income, amount):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add(
            Recurring(
                description="R",
                amount=amount,
                start_date=datetime(2023, 1, 1),
                frequency="monthly",
            )
        )
        session.commit()
        session.close()

        responses = [("delete", 0), None]

        def fake_scroll(stdscr, entries, index, **kwargs):
            return responses.pop(0)

        monkeypatch.setattr(cli, "scroll_menu", fake_scroll)
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "confirm", lambda stdscr, msg: True)

        cli.edit_recurring(object(), is_income)

        session = Session()
        assert session.query(Recurring).count() == 0
    finally:
        session.close()
        path.unlink()
