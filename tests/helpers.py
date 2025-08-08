import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

# Ensure the project root is on the Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import database


def get_temp_session():
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    TestingSession = sessionmaker(bind=engine)
    database.Base.metadata.create_all(engine)
    return TestingSession, Path(db_path)


@pytest.fixture(scope="module")
def session_factory():
    """Provide a sessionmaker bound to an in-memory SQLite engine."""
    engine = create_engine("sqlite:///:memory:", future=True)
    database.Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    yield TestingSession
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(session_factory):
    """Ensure a clean database state for each test."""
    yield
    session = session_factory()
    database.Base.metadata.drop_all(bind=session.bind)
    database.Base.metadata.create_all(bind=session.bind)
    session.close()


def make_prompt(responses):
    iterator = iter(responses)

    def _prompt(*args, **kwargs):
        return next(iterator)

    return _prompt
