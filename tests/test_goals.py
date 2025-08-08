from datetime import datetime

from tests.helpers import get_temp_session, make_prompt
from budget import cli
from budget.models import Goal


def test_add_goal(monkeypatch):
    Session, path = get_temp_session()
    try:
        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(
            cli,
            "goal_form",
            lambda stdscr, desc, date, amount, enabled: (
                "Save",
                datetime(2023, 1, 1),
                50.0,
                True,
            ),
        )
        cli.add_goal(object())
        session = Session()
        goals = session.query(Goal).all()
        assert len(goals) == 1
        g = goals[0]
        assert g.description == "Save"
        assert g.amount == 50.0
        assert g.target_date.date() == datetime(2023, 1, 1).date()
        assert g.enabled is True
    finally:
        session.close()
        path.unlink()


def test_goal_form_toggle(monkeypatch):
    monkeypatch.setattr(cli, "select", make_prompt(["enabled", "save"]))
    monkeypatch.setattr(cli, "text", make_prompt([]))
    result = cli.goal_form(object(), "Desc", datetime(2023, 1, 1), 10.0, True)
    assert result == ("Desc", datetime(2023, 1, 1), 10.0, False)


def test_wants_goals_menu_columns(monkeypatch):
    Session, path = get_temp_session()
    try:
        session = Session()
        session.add_all(
            [
                Goal(
                    description="G1",
                    amount=10.0,
                    target_date=datetime(2023, 1, 1),
                    enabled=True,
                ),
                Goal(
                    description="G2",
                    amount=5.5,
                    target_date=datetime(2023, 1, 2),
                    enabled=False,
                ),
            ]
        )
        session.commit()
        session.close()

        captured = {}

        def fake_curses(stdscr, entries, index, header=None, footer_right=""):
            captured["entries"] = entries
            captured["index"] = index
            return ("quit", None)

        monkeypatch.setattr(cli, "SessionLocal", Session)
        monkeypatch.setattr(cli, "goals_curses", fake_curses)

        cli.wants_goals_menu(object())

        first = captured["entries"][0].split("|")
        assert first[0].strip() == "2023-01-01"
        assert first[1].strip() == "10.00"
        assert first[2].strip() == "on"
    finally:
        path.unlink()
