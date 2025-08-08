from tests import helpers  # noqa: F401  # ensure project root on path

from budget.models import IrregularCategory, IrregularRule
from budget.services_irregular import rules_for, match_category_id


def test_rules_for_and_match_category():
    TestingSession, db_path = helpers.get_temp_session()
    session = TestingSession()

    cat1 = IrregularCategory(name="Auto")
    cat2 = IrregularCategory(name="Health")
    session.add_all([cat1, cat2])
    session.commit()

    session.add_all(
        [
            IrregularRule(category_id=cat1.id, pattern="auto"),
            IrregularRule(category_id=cat2.id, pattern="dentist"),
        ]
    )
    session.commit()

    assert rules_for(session, cat1.id) == ["auto"]
    assert match_category_id(session, "Paid AUTO shop") == cat1.id
    assert match_category_id(session, "dentist appointment") == cat2.id
    assert match_category_id(session, "unknown") is None

    session.close()
    db_path.unlink()

