"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime, date, timedelta
import curses
import calendar
from dataclasses import dataclass
from bisect import bisect_left, bisect_right

from .database import SessionLocal, init_db
from .models import Transaction, Balance, Recurring, Goal

FREQUENCIES = [
    "weekly",
    "biweekly",
    "semi monthly",
    "monthly",
    "quarterly",
    "semi annually",
    "annually",
]


def select(message, choices, default=None):
    """Display a scrollable menu and return the selected value.

    ``choices`` may be a list of strings or ``(title, value)`` pairs. The menu
    is navigated with the arrow keys and the highlighted entry is returned when
    the user presses Enter. ``default`` selects the initially highlighted value
    if provided.
    """

    titles: list[str] = []
    values = []
    default_idx = 0
    for idx, choice in enumerate(choices):
        if isinstance(choice, tuple):
            title, value = choice
        else:
            title = value = choice
        titles.append(title)
        values.append(value)
        if default is not None and value == default:
            default_idx = idx

    with SessionLocal() as s:
        bal = s.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
    selected = scroll_menu(
        titles,
        default_idx,
        header=message,
        footer_right=f"{bal_amt:.2f}",
    )
    if selected is None:
        return None
    return values[selected]


def _center_box(stdscr, height: int, width: int) -> "curses.window":
    """Create a bordered window centered on ``stdscr``."""

    h, w = stdscr.getmaxyx()
    height = min(height, h)
    width = min(width, w)
    y = max(0, (h - height) // 2)
    x = max(0, (w - width) // 2)
    win = curses.newwin(height, width, y, x)
    win.box()
    return win


def text(message, default=None):
    """Prompt the user for free-form text input using a boxed overlay."""

    def _prompt(stdscr):
        curses.curs_set(1)
        stdscr.keypad(True)
        h, w = stdscr.getmaxyx()
        h = max(1, h)
        w = max(1, w)
        prompt = f"{message}" + (f" [{default}]" if default is not None else "") + ": "
        input_width = max(1, min(40, w - len(prompt) - 6))
        box_width = len(prompt) + input_width + 4
        win = _center_box(stdscr, 3, box_width)
        try:
            win.addnstr(1, 2, prompt, box_width - 4)
        except curses.error:
            pass
        win.refresh()
        curses.echo()
        try:
            resp = win.getstr(1, 2 + len(prompt), input_width)
        except curses.error:
            resp = b""
        finally:
            curses.noecho()
        text = resp.decode()
        if text == "" and default is not None:
            return default
        return text

    return curses.wrapper(_prompt)


def confirm(message: str) -> bool:
    """Prompt the user to confirm an action within a centered box."""

    def _prompt(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        lines = [message, "Press Enter to confirm, or any other key to cancel."]
        max_line = max(len(line) for line in lines)
        win = _center_box(stdscr, len(lines) + 2, max_line + 4)
        for idx, line in enumerate(lines):
            x = (max_line - len(line)) // 2 + 2
            try:
                win.addnstr(1 + idx, x, line, max_line)
            except curses.error:
                pass
        win.refresh()
        ch = win.getch()
        return ch in (curses.KEY_ENTER, 10, 13)

    return curses.wrapper(_prompt)


def transaction_form(
    description: str, timestamp: datetime, amount: float
):
    """Interactive form for editing transaction fields.

    Returns ``(description, timestamp, amount)`` if saved, otherwise ``None``.
    """

    while True:
        choice = select(
            "Select field to edit",
            choices=[
                (f"Name: {description}", "description"),
                (f"Date: {timestamp.strftime('%Y-%m-%d')}", "date"),
                (f"Amount: {amount}", "amount"),
                ("Save", "save"),
                ("Cancel", "cancel"),
            ],
        )

        if choice == "description":
            new_desc = text("Description", default=description)
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = text(
                "Date (YYYY-MM-DD)", default=timestamp.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.")
        elif choice == "amount":
            amount_str = text("Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    print("Invalid amount.")
        elif choice == "save":
            return description, timestamp, amount
        else:
            return None


def add_transaction() -> None:
    """Prompt user for transaction data and persist it."""
    form = transaction_form("", datetime.utcnow(), 0.0)
    if form is None:
        return
    description, timestamp, amount = form
    session = SessionLocal()
    txn = Transaction(description=description, amount=amount, timestamp=timestamp)
    session.add(txn)
    session.commit()
    session.close()
    print("Transaction recorded.\n")


def add_recurring(is_income: bool, existing: Recurring | None = None) -> None:
    """Prompt user to add or edit a recurring bill or income."""

    name = text("Name", default=existing.description if existing else None)
    if name is None:
        return
    date_str = text(
        "Start date (YYYY-MM-DD)",
        default=existing.start_date.strftime("%Y-%m-%d") if existing else None,
    )
    if date_str is None:
        return
    amount_str = text(
        "Amount",
        default=str(abs(existing.amount)) if existing else None,
    )
    if amount_str is None:
        return
    freq = select(
        "Frequency",
        FREQUENCIES,
        default=existing.frequency if existing else None,
    )
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        amount = float(amount_str)
    except ValueError:
        print("Invalid input.")
        return
    amount = abs(amount) if is_income else -abs(amount)
    session = SessionLocal()
    if existing is None:
        rec = Recurring(
            description=name, amount=amount, start_date=start, frequency=freq
        )
        session.add(rec)
    else:
        rec = session.get(Recurring, existing.id)
        if rec is None:
            session.close()
            return
        rec.description = name
        rec.amount = amount
        rec.start_date = start
        rec.frequency = freq
    session.commit()
    session.close()
    print("Recurring item recorded.\n")


def goal_form(
    description: str,
    target_date: datetime,
    amount: float,
    enabled: bool,
):
    """Interactive form for editing goal fields."""

    while True:
        choice = select(
            "Select field to edit",
            choices=[
                (f"Name: {description}", "description"),
                (f"Date: {target_date.strftime('%Y-%m-%d')}", "date"),
                (f"Amount: {amount}", "amount"),
                (f"Enabled: {'on' if enabled else 'off'}", "enabled"),
                ("Save", "save"),
                ("Cancel", "cancel"),
            ],
        )

        if choice == "description":
            new_desc = text("Description", default=description)
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = text(
                "Date (YYYY-MM-DD)", default=target_date.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    target_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    print("Invalid date format. Use YYYY-MM-DD.")
        elif choice == "amount":
            amount_str = text("Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    print("Invalid amount.")
        elif choice == "enabled":
            enabled = not enabled
        elif choice == "save":
            return description, target_date, amount, enabled
        else:
            return None


def add_goal(existing: Goal | None = None) -> None:
    """Prompt user to add or edit a goal."""

    form = goal_form(
        existing.description if existing else "",
        existing.target_date if existing else datetime.utcnow(),
        existing.amount if existing else 0.0,
        existing.enabled if existing else True,
    )
    if form is None:
        return
    description, target_date, amount, enabled = form
    session = SessionLocal()
    if existing is None:
        goal = Goal(
            description=description,
            amount=amount,
            target_date=target_date,
            enabled=enabled,
        )
        session.add(goal)
    else:
        goal = session.get(Goal, existing.id)
        if goal is None:
            session.close()
            return
        goal.description = description
        goal.target_date = target_date
        goal.amount = amount
        goal.enabled = enabled
    session.commit()
    session.close()
    print("Goal recorded.\n")


def edit_recurring(is_income: bool) -> None:
    """Edit or add recurring bills/incomes."""

    session = SessionLocal()
    while True:
        recs = (
            session.query(Recurring)
            .filter(Recurring.amount > 0 if is_income else Recurring.amount < 0)
            .order_by(Recurring.start_date)
            .all()
        )
        desc_w = max((len(r.description) for r in recs), default=0)
        amt_w = max((len(f"{r.amount:.2f}") for r in recs), default=0)
        entries = [
            f"{r.start_date.strftime('%Y-%m-%d')} | {r.description:<{desc_w}} | {r.amount:>{amt_w}.2f}"
            for r in recs
        ]
        entries.append("Back")
        bal = session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        res = scroll_menu(
            entries,
            0,
            header="Edit income" if is_income else "Edit bills",
            footer_left="Select to edit, 'a' to add, 'd' to delete",
            footer_right=f"{bal_amt:.2f}",
            allow_add=True,
            allow_delete=True,
        )
        if isinstance(res, tuple) and res[0] == "delete":
            del_idx = res[1]
            if del_idx < len(recs):
                rec = recs[del_idx]
                if confirm("Delete this item?"):
                    session.delete(rec)
                    session.commit()
            session.close()
            session = SessionLocal()
            continue
        idx = res
        if idx == -1:
            session.close()
            add_recurring(is_income)
            session = SessionLocal()
            continue
        if idx is None or idx >= len(recs):
            break
        rec = recs[idx]
        session.close()
        add_recurring(is_income, rec)
        session = SessionLocal()
    session.close()


def edit_transaction(session, txn: Transaction) -> None:
    """Edit an existing transaction in-place."""
    form = transaction_form(txn.description, txn.timestamp, txn.amount)
    if form is None:
        return
    description, timestamp, amount = form
    txn.description = description
    txn.timestamp = timestamp
    txn.amount = amount
    session.commit()


def list_transactions() -> None:
    """List all transactions in the database and allow editing."""
    session = SessionLocal()
    while True:
        txns = session.query(Transaction).order_by(Transaction.timestamp).all()
        if not txns:
            print("No transactions recorded yet.\n")
            break
        desc_w = max(len(t.description) for t in txns)
        amt_w = max(len(f"{t.amount:.2f}") for t in txns)
        entries = [
            f"{t.timestamp.strftime('%Y-%m-%d')} | {t.description:<{desc_w}} | {t.amount:>{amt_w}.2f}"
            for t in txns
        ]
        entries.append("Back")
        bal = session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        res = scroll_menu(
            entries,
            0,
            header="Select transaction to edit",
            footer_left="Select to edit, 'd' to delete",
            footer_right=f"{bal_amt:.2f}",
            allow_delete=True,
        )
        if isinstance(res, tuple) and res[0] == "delete":
            del_idx = res[1]
            if del_idx < len(txns):
                txn = txns[del_idx]
                if confirm("Delete this transaction?"):
                    session.delete(txn)
                    session.commit()
            continue
        idx = res
        if idx is None or idx >= len(txns):
            break
        txn = txns[idx]
        edit_transaction(session, txn)
    session.close()


def set_balance() -> None:
    """Prompt the user to store their current balance."""
    amount_str = text("Current balance")
    if amount_str is None:
        return
    try:
        amount = float(amount_str)
    except ValueError:
        print("Invalid amount.")
        return
    session = SessionLocal()
    bal = session.get(Balance, 1)
    if bal is None:
        bal = Balance(id=1, amount=amount, timestamp=datetime.utcnow())
        session.add(bal)
    else:
        bal.amount = amount
        bal.timestamp = datetime.utcnow()
    session.commit()
    session.close()


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_date(d: date, freq: str) -> date:
    if freq == "weekly":
        return d + timedelta(weeks=1)
    if freq == "biweekly":
        return d + timedelta(weeks=2)
    if freq == "semi monthly":
        return d + timedelta(days=15)
    if freq == "monthly":
        return add_months(d, 1)
    if freq == "quarterly":
        return add_months(d, 3)
    if freq == "semi annually":
        return add_months(d, 6)
    if freq == "annually":
        return add_months(d, 12)
    return d


def retreat_date(d: date, freq: str) -> date:
    if freq == "weekly":
        return d - timedelta(weeks=1)
    if freq == "biweekly":
        return d - timedelta(weeks=2)
    if freq == "semi monthly":
        return d - timedelta(days=15)
    if freq == "monthly":
        return add_months(d, -1)
    if freq == "quarterly":
        return add_months(d, -3)
    if freq == "semi annually":
        return add_months(d, -6)
    if freq == "annually":
        return add_months(d, -12)
    return d


def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def occurrence_on_or_before(start: date, freq: str, target: date) -> date | None:
    if target < start:
        return None
    if freq == "weekly":
        weeks = (target - start).days // 7
        return start + timedelta(weeks=weeks)
    if freq == "biweekly":
        weeks = (target - start).days // 14
        return start + timedelta(weeks=weeks * 2)
    if freq == "semi monthly":
        days = (target - start).days // 15
        return start + timedelta(days=days * 15)
    step_map = {"monthly": 1, "quarterly": 3, "semi annually": 6, "annually": 12}
    step = step_map.get(freq)
    if step:
        months = months_between(start, target)
        months = (months // step) * step
        occ = add_months(start, months)
        if occ > target:
            months -= step
            if months < 0:
                return None
            occ = add_months(start, months)
        return occ
    return start


def occurrence_after(start: date, freq: str, after: date) -> date | None:
    if after < start:
        return start
    if freq == "weekly":
        weeks = (after - start).days // 7 + 1
        return start + timedelta(weeks=weeks)
    if freq == "biweekly":
        weeks = (after - start).days // 14 + 1
        return start + timedelta(weeks=weeks * 2)
    if freq == "semi monthly":
        days = (after - start).days // 15 + 1
        return start + timedelta(days=days * 15)
    step_map = {"monthly": 1, "quarterly": 3, "semi annually": 6, "annually": 12}
    step = step_map.get(freq)
    if step:
        months = months_between(start, after)
        months = ((months // step) + 1) * step
        return add_months(start, months)
    return None


def count_occurrences(start: date, freq: str, target: date) -> int:
    if target < start:
        return 0
    if freq == "weekly":
        return (target - start).days // 7 + 1
    if freq == "biweekly":
        return (target - start).days // 14 + 1
    if freq == "semi monthly":
        return (target - start).days // 15 + 1
    step_map = {"monthly": 1, "quarterly": 3, "semi annually": 6, "annually": 12}
    step = step_map.get(freq)
    if step:
        months = months_between(start, target)
        occ = months // step
        occ_date = add_months(start, occ * step)
        if occ_date > target:
            occ -= 1
        return max(0, occ) + 1
    return 1


def next_event(after: datetime, txns, recs):
    next_txn = None
    txn_times = [t.timestamp for t in txns]
    idx = bisect_right(txn_times, after)
    if idx < len(txns):
        next_txn = txns[idx]
    next_rec = None
    next_rec_time = None
    for r in recs:
        occ = occurrence_after(r.start_date.date(), r.frequency, after.date())
        if occ is None:
            continue
        occ_dt = datetime.combine(occ, datetime.min.time())
        if next_rec_time is None or occ_dt < next_rec_time:
            next_rec_time = occ_dt
            next_rec = r
    if next_txn is None and next_rec is None:
        return None
    if next_txn is not None and (next_rec_time is None or next_txn.timestamp <= next_rec_time):
        return next_txn.timestamp, next_txn.description, next_txn.amount
    return next_rec_time, next_rec.description, next_rec.amount


def prev_event(before: datetime, txns, recs):
    prev_txn = None
    txn_times = [t.timestamp for t in txns]
    idx = bisect_left(txn_times, before) - 1
    if idx >= 0:
        prev_txn = txns[idx]
    prev_rec = None
    prev_rec_time = None
    for r in recs:
        target = before.date()
        if before.time() == datetime.min.time():
            target -= timedelta(days=1)
        occ = occurrence_on_or_before(r.start_date.date(), r.frequency, target)
        if occ is None:
            continue
        occ_dt = datetime.combine(occ, datetime.min.time())
        if prev_rec_time is None or occ_dt > prev_rec_time:
            prev_rec_time = occ_dt
            prev_rec = r
    if prev_txn is None and prev_rec is None:
        return None
    if prev_txn is not None and (prev_rec_time is None or prev_txn.timestamp >= prev_rec_time):
        return prev_txn.timestamp, prev_txn.description, prev_txn.amount
    return prev_rec_time, prev_rec.description, prev_rec.amount


@dataclass
class LedgerRow:
    timestamp: datetime
    description: str
    amount: float
    running: float

    @property
    def date(self) -> date:
        return self.timestamp.date()


def ledger_rows(session):
    bal = session.get(Balance, 1)
    bal_amt = bal.amount if bal else 0.0
    bal_ts = bal.timestamp if bal and bal.timestamp else datetime.combine(date.today(), datetime.min.time())
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    recs = session.query(Recurring).all()

    def total_up_to(ts: datetime) -> float:
        total = 0.0
        for t in txns:
            if t.timestamp <= ts:
                total += t.amount
        for r in recs:
            total += count_occurrences(r.start_date.date(), r.frequency, ts.date()) * r.amount
        return total

    offset = bal_amt - total_up_to(bal_ts)
    txn_iter = iter(txns)
    next_txn = next(txn_iter, None)
    occurrences = [(r.start_date, r) for r in recs]
    running = 0.0

    while True:
        next_ts: datetime | None = None
        next_kind = None
        if next_txn is not None:
            next_ts = next_txn.timestamp
            next_kind = ("txn", next_txn)
        for idx, (occ_dt, rec) in enumerate(occurrences):
            if next_ts is None or occ_dt <= next_ts:
                next_ts = occ_dt
                next_kind = ("rec", idx)
        if next_kind is None:
            break
        if next_kind[0] == "txn":
            running += next_txn.amount
            yield LedgerRow(next_txn.timestamp, next_txn.description, next_txn.amount, running + offset)
            next_txn = next(txn_iter, None)
        else:
            idx = next_kind[1]
            occ_dt, rec = occurrences[idx]
            running += rec.amount
            yield LedgerRow(occ_dt, rec.description, rec.amount, running + offset)
            next_occ = datetime.combine(advance_date(occ_dt.date(), rec.frequency), datetime.min.time())
            occurrences[idx] = (next_occ, rec)


def ledger_curses(initial_row, get_prev, get_next, bal_amt):
    def _view(stdscr):
        rows = [initial_row]
        index = 0
        curses.curs_set(0)
        stdscr.keypad(True)

        desc_w = len(initial_row.description)
        amt_w = len(f"{initial_row.amount:.2f}")
        run_w = len(f"{initial_row.running:.2f}")
        footer_left = date.today().isoformat()
        footer_right = f"{bal_amt:.2f}"

        while True:
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            visible = h - 1

            while index < visible // 2:
                prev = get_prev(rows[0].timestamp)
                if prev is None:
                    break
                prev_row = LedgerRow(
                    prev[0], prev[1], prev[2], rows[0].running - rows[0].amount
                )
                rows.insert(0, prev_row)
                desc_w = max(desc_w, len(prev_row.description))
                amt_w = max(amt_w, len(f"{prev_row.amount:.2f}"))
                run_w = max(run_w, len(f"{prev_row.running:.2f}"))
                index += 1

            while len(rows) < visible:
                nxt = get_next(rows[-1].timestamp)
                if nxt is None:
                    break
                next_row = LedgerRow(
                    nxt[0], nxt[1], nxt[2], rows[-1].running + nxt[2]
                )
                rows.append(next_row)
                desc_w = max(desc_w, len(next_row.description))
                amt_w = max(amt_w, len(f"{next_row.amount:.2f}"))
                run_w = max(run_w, len(f"{next_row.running:.2f}"))

            top = min(max(0, index - visible // 2), max(0, len(rows) - visible))

            stdscr.erase()
            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(rows):
                    break
                r = rows[line_idx]
                line = (
                    f"{r.date.strftime('%Y-%m-%d')} | "
                    f"{r.description:<{desc_w}} | "
                    f"{r.amount:>{amt_w}.2f} | {r.running:>{run_w}.2f}"
                )
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addnstr(i, 0, line, w - 1, attr)
                except curses.error:
                    pass

            try:
                stdscr.addnstr(h - 1, 0, footer_left, max(0, w))
                stdscr.addnstr(
                    h - 1,
                    max(0, w - len(footer_right)),
                    footer_right,
                    len(footer_right),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP:
                if index > 0:
                    index -= 1
                else:
                    prev = get_prev(rows[0].timestamp)
                    if prev is not None:
                        prev_row = LedgerRow(
                            prev[0], prev[1], prev[2], rows[0].running - rows[0].amount
                        )
                        rows.insert(0, prev_row)
                        desc_w = max(desc_w, len(prev_row.description))
                        amt_w = max(amt_w, len(f"{prev_row.amount:.2f}"))
                        run_w = max(run_w, len(f"{prev_row.running:.2f}"))
            elif key == curses.KEY_DOWN:
                if index < len(rows) - 1:
                    index += 1
                else:
                    nxt = get_next(rows[-1].timestamp)
                    if nxt is not None:
                        next_row = LedgerRow(
                            nxt[0], nxt[1], nxt[2], rows[-1].running + nxt[2]
                        )
                        rows.append(next_row)
                        desc_w = max(desc_w, len(next_row.description))
                        amt_w = max(amt_w, len(f"{next_row.amount:.2f}"))
                        run_w = max(run_w, len(f"{next_row.running:.2f}"))
                        index += 1
            elif key == ord("q"):
                break

    return curses.wrapper(_view)


def scroll_menu(
    entries,
    index,
    height: int | None = None,
    header: str | None = None,
    footer_left: str | None = None,
    footer_right: str | None = None,
    allow_add: bool = False,
    allow_delete: bool = False,
):
    """Display ``entries`` in a curses-driven scrollable window.

    A footer is rendered on the bottom line that shows today's date and the
    stored account balance in the lower-right corner. When ``height`` is not
    provided the list fills the available screen height; otherwise it is limited
    to the given number of rows.
    """

    def _menu(stdscr):
        nonlocal index
        curses.curs_set(0)
        stdscr.keypad(True)

        footer_l = footer_left if footer_left is not None else date.today().isoformat()
        footer_r = footer_right if footer_right is not None else ""

        while True:  # redraw loop
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            offset = 1 if header else 0
            visible = min(len(entries), height or (h - 1 - offset))
            top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

            stdscr.erase()
            if header:
                head_x = max(0, (w - len(header)) // 2)
                try:
                    stdscr.addnstr(0, head_x, header, max(0, w - head_x))
                except curses.error:
                    pass
            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(entries):
                    break
                line = entries[line_idx]
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addstr(i + offset, 0, line[: w - 1], attr)
                except curses.error:
                    pass

            try:
                stdscr.addnstr(h - 1, 0, footer_l, max(0, w))
                stdscr.addnstr(
                    h - 1,
                    max(0, w - len(footer_r)),
                    footer_r,
                    len(footer_r),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                return index
            elif key == ord("a") and allow_add:
                return -1
            elif key == ord("d") and allow_delete:
                return ("delete", index)
            elif key == ord("q"):
                return None

            if index < top:
                top = index
            elif index >= top + visible:
                top = index - visible + 1

    return curses.wrapper(_menu)


def ledger_view() -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""

    session = SessionLocal()
    bal = session.get(Balance, 1)
    bal_amt = bal.amount if bal else 0.0
    bal_ts = bal.timestamp if bal and bal.timestamp else datetime.combine(date.today(), datetime.min.time())
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()
    recs = session.query(Recurring).all()
    today = date.today()
    start_ev = prev_event(datetime.combine(today + timedelta(days=1), datetime.min.time()), txns, recs)
    if start_ev is None:
        start_ev = next_event(datetime.combine(today - timedelta(days=1), datetime.min.time()), txns, recs)
    if start_ev is None:
        print("No transactions recorded yet.\n")
        session.close()
        return
    start_ts, start_desc, start_amt = start_ev

    def total_up_to(ts: datetime) -> float:
        total = 0.0
        for t in txns:
            if t.timestamp <= ts:
                total += t.amount
        for r in recs:
            total += count_occurrences(r.start_date.date(), r.frequency, ts.date()) * r.amount
        return total

    offset = bal_amt - total_up_to(bal_ts)
    start_running = total_up_to(start_ts) + offset
    initial_row = LedgerRow(start_ts, start_desc, start_amt, start_running)

    def get_next(ts_after):
        return next_event(ts_after, txns, recs)

    def get_prev(ts_before):
        return prev_event(ts_before, txns, recs)

    session.close()
    ledger_curses(initial_row, get_prev, get_next, bal_amt)


def goals_curses(entries, index, header=None, footer_right=""):
    """Display goals list with controls for add, delete, edit, and toggle."""

    def _menu(stdscr):
        nonlocal index
        curses.curs_set(0)
        stdscr.keypad(True)

        while True:
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            offset = 1 if header else 0
            visible = min(len(entries), h - 1 - offset)
            top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

            stdscr.erase()
            if header:
                head_x = max(0, (w - len(header)) // 2)
                try:
                    stdscr.addnstr(0, head_x, header, max(0, w - head_x))
                except curses.error:
                    pass
            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(entries):
                    break
                line = entries[line_idx]
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addnstr(i + offset, 0, line, w - 1, attr)
                except curses.error:
                    pass

            footer_l = date.today().isoformat()
            try:
                stdscr.addnstr(h - 1, 0, footer_l, max(0, w))
                stdscr.addnstr(
                    h - 1,
                    max(0, w - len(footer_right)),
                    footer_right,
                    len(footer_right),
                )
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                return "edit", index
            elif key == ord("a"):
                return "add", None
            elif key == ord("d") and entries:
                return "delete", index
            elif key == ord("t") and entries:
                return "toggle", index
            elif key == ord("q"):
                return "quit", None

            if index < top:
                top = index
            elif index >= top + visible:
                top = index - visible + 1

    return curses.wrapper(_menu)


def wants_goals_menu() -> None:
    """Display and manage user goals."""

    session = SessionLocal()
    index = 0
    while True:
        goals = session.query(Goal).order_by(Goal.target_date).all()
        amt_w = max((len(f"{g.amount:.2f}") for g in goals), default=0)
        entries = [
            f"{g.target_date.strftime('%Y-%m-%d')} | {g.amount:>{amt_w}.2f} | {'on' if g.enabled else 'off'} | {g.description}"
            for g in goals
        ]
        bal = session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        action, idx = goals_curses(
            entries,
            index,
            header="Goals (Enter=edit, a=add, d=del, t=toggle)",
            footer_right=f"{bal_amt:.2f}",
        )
        if action == "add":
            session.close()
            add_goal()
            session = SessionLocal()
            index = len(goals)
        elif action == "edit" and goals:
            g = goals[idx]
            session.close()
            add_goal(g)
            session = SessionLocal()
            index = idx
        elif action == "delete" and goals:
            session.delete(goals[idx])
            session.commit()
            index = max(0, min(idx, len(goals) - 2))
        elif action == "toggle" and goals:
            goal = goals[idx]
            goal.enabled = not goal.enabled
            session.commit()
            index = idx
        else:  # quit
            break
    session.close()


def main() -> None:
    init_db()
    while True:
        choice = select(
            "Select an option",
            choices=[
                "Enter transaction",
                "List transactions",
                "Edit bills",
                "Edit income",
                "Ledger",
                "Set balance",
                "Wants/Goals",
                "Quit",
            ],
        )
        if choice == "Enter transaction":
            add_transaction()
        elif choice == "List transactions":
            list_transactions()
        elif choice == "Edit bills":
            edit_recurring(False)
        elif choice == "Edit income":
            edit_recurring(True)
        elif choice == "Ledger":
            ledger_view()
        elif choice == "Set balance":
            set_balance()
        elif choice == "Wants/Goals":
            wants_goals_menu()
        else:
            break


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
