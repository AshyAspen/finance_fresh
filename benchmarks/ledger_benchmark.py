import os
import time
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from budget import database, cli
from budget.models import Balance, Transaction


def build_session(n_days: int, events_per_day: int):
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    database.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(Balance(id=1, amount=0.0, timestamp=datetime(2023, 1, 1)))
    start = datetime(2023, 1, 1)
    txns = []
    for day in range(n_days):
        for ev in range(events_per_day):
            ts = start + timedelta(days=day, minutes=ev)
            txns.append(Transaction(description=f"T{day}-{ev}", amount=1.0, timestamp=ts))
    session.add_all(txns)
    session.commit()
    return session, Path(db_path)


def run():
    session, path = build_session(365, 3)
    try:
        start = time.perf_counter()
        rows = list(cli.ledger_rows(session))
        duration = time.perf_counter() - start
        print(f"Generated {len(rows)} rows in {duration:.4f}s")
    finally:
        session.close()
        path.unlink()


if __name__ == "__main__":
    run()
