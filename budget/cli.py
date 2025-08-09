"""Command-line interface for budget app."""
from __future__ import annotations

from datetime import datetime, date, timedelta
import curses
import calendar
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
from curses import panel
from contextlib import contextmanager

from .database import SessionLocal, init_db
from .models import (
    Transaction,
    Balance,
    Recurring,
    Goal,
    IrregularCategory,
    IrregularRule,
    IrregularState,
)
from .services_irregular import (
    irregular_daily_series,
    learn_irregular_state,
    categories,
    match_category_id,
    update_irregular_state,
    get_or_create_state,
)
from .services import occurrences_between

FREQUENCIES = [
    "weekly",
    "biweekly",
    "semi monthly",
    "monthly",
    "quarterly",
    "semi annually",
    "annually",
]

# irregular forecast mode stored for session
IRREG_MODE = "monte_carlo"
IRREG_QUANTILE = "p80"

INITIAL_FORWARD_MONTHS = 18
EXTEND_CHUNK_MONTHS = 6
EDGE_TRIGGER_DAYS = 14


def select(stdscr, message, choices, default=None, boxed=True):
    """Display a scrollable menu and return the selected value.

    ``choices`` may be a list of strings or ``(title, value)`` pairs. The menu
    is navigated with the arrow keys and the highlighted entry is returned when
    the user presses Enter. ``default`` selects the initially highlighted value
    if provided. ``boxed`` renders the menu inside a centered bordered window
    when ``True``.
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
        stdscr,
        titles,
        default_idx,
        header=message,
        footer_right=f"{bal_amt:.2f}",
        boxed=boxed,
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


@contextmanager
def temp_cursor(state: int):
    """Temporarily set cursor visibility and restore on exit."""

    prev = None
    try:
        prev = curses.curs_set(state)
    except curses.error:  # pragma: no cover - some terminals
        prev = None
    try:
        yield
    finally:
        if prev is not None:
            try:
                curses.curs_set(prev)
            except curses.error:  # pragma: no cover - cleanup best effort
                pass


@contextmanager
def keypad_mode(win):
    """Enable keypad mode and ensure it is disabled afterwards."""

    try:
        win.keypad(True)
    except curses.error:  # pragma: no cover - fake windows
        pass
    try:
        yield
    finally:
        try:
            win.keypad(False)
        except curses.error:  # pragma: no cover - fake windows
            pass


@contextmanager
def modal_box(stdscr, height: int, width: int):
    """Create a centered, boxed modal window backed by a panel; auto-cleans on exit."""
    win = _center_box(stdscr, height, width)
    try:
        pnl = panel.new_panel(win)
    except Exception:  # pragma: no cover - non-curses window in tests
        pnl = None
    if pnl is not None:
        panel.update_panels()
        curses.doupdate()
    try:
        with keypad_mode(win):
            yield win
    finally:
        try:
            win.erase()
            win.noutrefresh()
        except (curses.error, AttributeError):  # pragma: no cover - fake windows
            pass
        if pnl is not None:
            pnl.hide()
            panel.update_panels()
            curses.doupdate()

def text(stdscr, message, default=None):
    with temp_cursor(1), keypad_mode(stdscr):
        h, w = stdscr.getmaxyx()
        prompt = f"{message}" + (f" [{default}]" if default is not None else "") + ": "
        input_width = max(1, min(40, w - len(prompt) - 6))
        box_width = len(prompt) + input_width + 4
        # Guard against resize between measurement and drawing
        _, w = stdscr.getmaxyx()
        box_width = min(box_width, w)

        with modal_box(stdscr, 3, box_width) as win:
            try:
                win.addnstr(1, 2, prompt, box_width - 4)
            except curses.error:
                pass
            try:
                win.refresh()
            except curses.error:
                pass
            curses.echo()
            try:
                resp = win.getstr(1, 2 + len(prompt), input_width)
            except curses.error:
                resp = b""
            finally:
                curses.noecho()

        text_val = resp.decode()
    return default if text_val == "" and default is not None else text_val


def confirm(stdscr, message: str) -> bool:
    lines = [message, "Press Enter to confirm, or any other key to cancel."]
    max_line = max(len(line) for line in lines)

    with temp_cursor(0), keypad_mode(stdscr):
        with modal_box(stdscr, len(lines) + 2, max_line + 4) as win:
            for idx, line in enumerate(lines):
                x = (max_line - len(line)) // 2 + 2
                try:
                    win.addnstr(1 + idx, x, line, max_line)
                except curses.error:
                    pass
            try:
                win.refresh()
            except curses.error:
                pass
            ch = win.getch()
    return ch in (curses.KEY_ENTER, 10, 13)


def toast(stdscr, msg: str, ms: int = 900):
    h, w = stdscr.getmaxyx()
    box_w = min(max(len(msg) + 4, 12), max(12, w - 2))
    with modal_box(stdscr, 3, box_w) as win:
        try:
            win.addnstr(1, 2, msg[: box_w - 4], box_w - 4)
        except curses.error:
            pass
        curses.napms(ms)


def transaction_form(
    stdscr, description: str, timestamp: datetime, amount: float
):
    """Interactive form for editing transaction fields.

    Returns ``(description, timestamp, amount)`` if saved, otherwise ``None``.
    """

    while True:
        choice = select(
            stdscr,
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
            new_desc = text(stdscr, "Description", default=description)
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = text(
                stdscr,
                "Date (YYYY-MM-DD)", default=timestamp.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    timestamp = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    pass
        elif choice == "amount":
            amount_str = text(stdscr, "Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    pass
        elif choice == "save":
            return description, timestamp, amount
        else:
            return None


def add_transaction(stdscr) -> None:
    """Prompt user for transaction data and persist it."""
    form = transaction_form(stdscr, "", datetime.utcnow(), 0.0)
    if form is None:
        return
    description, timestamp, amount = form
    session = SessionLocal()
    txn = Transaction(description=description, amount=amount, timestamp=timestamp)
    session.add(txn)
    session.commit()

    category_id = match_category_id(session, txn.description)
    if category_id is not None:
        state = get_or_create_state(session, category_id)
        update_irregular_state(state, txn)
        session.commit()
        cat = session.get(IrregularCategory, category_id)
        if cat is not None:
            avg = state.avg_gap_days if state.avg_gap_days is not None else 0.0
            med = state.median_amount if state.median_amount is not None else 0.0
            toast(
                stdscr,
                f"Updated \u2018{cat.name}\u2019: avg gap \u2192 {avg:.1f} days, median \u2192 ${med:.2f}",
            )

    session.close()


def add_recurring(stdscr, is_income: bool, existing: Recurring | None = None) -> None:
    """Prompt user to add or edit a recurring bill or income."""

    name = text(stdscr, "Name", default=existing.description if existing else None)
    if name is None:
        return
    date_str = text(
        stdscr,
        "Start date (YYYY-MM-DD)",
        default=existing.start_date.strftime("%Y-%m-%d") if existing else None,
    )
    if date_str is None:
        return
    amount_str = text(
        stdscr,
        "Amount",
        default=str(abs(existing.amount)) if existing else None,
    )
    if amount_str is None:
        return
    freq = select(
        stdscr,
        "Frequency",
        FREQUENCIES,
        default=existing.frequency if existing else None,
    )
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d")
        amount = float(amount_str)
    except ValueError:
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


def goal_form(
    stdscr,
    description: str,
    target_date: datetime,
    amount: float,
    enabled: bool,
):
    """Interactive form for editing goal fields."""

    while True:
        choice = select(
            stdscr,
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
            new_desc = text(stdscr, "Description", default=description)
            if new_desc is not None:
                description = new_desc
        elif choice == "date":
            date_str = text(
                stdscr,
                "Date (YYYY-MM-DD)", default=target_date.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    target_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    pass
        elif choice == "amount":
            amount_str = text(stdscr, "Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    pass
        elif choice == "enabled":
            enabled = not enabled
        elif choice == "save":
            return description, target_date, amount, enabled
        else:
            return None


def add_goal(stdscr, existing: Goal | None = None) -> None:
    """Prompt user to add or edit a goal."""

    form = goal_form(
        stdscr,
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


def edit_recurring(stdscr, is_income: bool) -> None:
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
        freq_w = max((len(r.frequency) for r in recs), default=0)
        amt_w = max((len(f"{r.amount:.2f}") for r in recs), default=0)
        entries = []
        for r in recs:
            anchor = r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
            next_occ = occurrences_between(
                anchor, r.frequency, date.today(), date.today() + timedelta(days=365)
            )
            next_str = next_occ[0].strftime("%Y-%m-%d") if next_occ else "n/a"
            entries.append(
                f"{r.start_date.strftime('%Y-%m-%d')} | {r.description:<{desc_w}} | {r.frequency:<{freq_w}} | {r.amount:>{amt_w}.2f} | Next occurrence: {next_str}"
            )
        entries.append("Back")
        bal = session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        res = scroll_menu(
            stdscr,
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
                if confirm(stdscr, "Delete this item?"):
                    session.delete(rec)
                    session.commit()
            session.close()
            session = SessionLocal()
            continue
        idx = res
        if idx == -1:
            session.close()
            add_recurring(stdscr, is_income)
            session = SessionLocal()
            continue
        if idx is None or idx >= len(recs):
            break
        rec = recs[idx]
        session.close()
        add_recurring(stdscr, is_income, rec)
        session = SessionLocal()
    session.close()


def edit_transaction(stdscr, session, txn: Transaction) -> None:
    """Edit an existing transaction in-place."""
    form = transaction_form(stdscr, txn.description, txn.timestamp, txn.amount)
    if form is None:
        return
    description, timestamp, amount = form
    txn.description = description
    txn.timestamp = timestamp
    txn.amount = amount
    session.commit()


def list_transactions(stdscr) -> None:
    """List all transactions in the database and allow editing."""
    session = SessionLocal()
    while True:
        txns = session.query(Transaction).order_by(Transaction.timestamp).all()
        desc_w = max((len(t.description) for t in txns), default=0)
        amt_w = max((len(f"{t.amount:.2f}") for t in txns), default=0)
        entries = [
            f"{t.timestamp.strftime('%Y-%m-%d')} | {t.description:<{desc_w}} | {t.amount:>{amt_w}.2f}"
            for t in txns
        ]
        entries.append("Back")
        bal = session.get(Balance, 1)
        bal_amt = bal.amount if bal else 0.0
        res = scroll_menu(
            stdscr,
            entries,
            0,
            header="Select transaction to edit",
            footer_left="Select to edit, 'a' to add, 'd' to delete",
            footer_right=f"{bal_amt:.2f}",
            allow_add=True,
            allow_delete=True,
        )
        if isinstance(res, tuple) and res[0] == "delete":
            del_idx = res[1]
            if del_idx < len(txns):
                txn = txns[del_idx]
                if confirm(stdscr, "Delete this transaction?"):
                    session.delete(txn)
                    session.commit()
            session.close()
            session = SessionLocal()
            continue
        if res == -1:
            session.close()
            add_transaction(stdscr)
            session = SessionLocal()
            continue
        idx = res
        if idx is None or idx >= len(txns):
            break
        txn = txns[idx]
        edit_transaction(stdscr, session, txn)
    session.close()


def set_balance(stdscr) -> None:
    """Prompt the user to store their current balance."""
    amount_str = text(stdscr, "Current balance")
    if amount_str is None:
        return
    try:
        amount = float(amount_str)
    except ValueError:
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


def settings_help_menu(stdscr) -> None:
    """Allow adjusting simple runtime settings."""

    global IRREG_MODE, IRREG_QUANTILE
    while True:
        mode_label = (
            "Deterministic"
            if IRREG_MODE == "deterministic"
            else ("Monte Carlo P50" if IRREG_QUANTILE == "p50" else "Monte Carlo P80")
        )
        choice = select(
            stdscr,
            "Settings / Help",
            [(f"Irregular forecast: {mode_label}", "toggle"), "Back"],
            boxed=False,
        )
        if choice == "toggle":
            if IRREG_MODE == "deterministic":
                IRREG_MODE = "monte_carlo"
                IRREG_QUANTILE = "p50"
            elif IRREG_QUANTILE == "p50":
                IRREG_QUANTILE = "p80"
            else:
                IRREG_MODE = "deterministic"
                IRREG_QUANTILE = "p80"
        else:
            break


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        day = last_day
    else:
        day = min(d.day, last_day)
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
        # round down to the nearest step interval
        months = (months // step) * step
        occ = add_months(start, months)
        # if the computed occurrence is not strictly after the target date,
        # advance by one additional interval
        if occ <= after:
            months += step
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
    for i, r in enumerate(recs):
        occ = occurrence_on_or_before(r.start_date.date(), r.frequency, after.date())
        if occ is not None:
            occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(microseconds=i)
            if occ_dt > after and (next_rec_time is None or occ_dt < next_rec_time):
                next_rec_time = occ_dt
                next_rec = r
                continue
        occ = occurrence_after(r.start_date.date(), r.frequency, after.date())
        if occ is None:
            continue
        occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(microseconds=i)
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
    for i, r in enumerate(recs):
        occ = occurrence_on_or_before(r.start_date.date(), r.frequency, before.date())
        if occ is not None:
            occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(microseconds=i)
            if occ_dt >= before:
                occ_prev = occurrence_on_or_before(
                    r.start_date.date(), r.frequency, before.date() - timedelta(days=1)
                )
                if occ_prev is None:
                    continue
                occ_dt = datetime.combine(occ_prev, datetime.min.time()) + timedelta(microseconds=i)
            if occ_dt < before and (prev_rec_time is None or occ_dt > prev_rec_time):
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


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def end_of_month(d: date, months: int = 0) -> date:
    d = add_months(d, months)
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def ledger_rows(session, plan_start: date | None = None, plan_end: date | None = None):
    bal = session.get(Balance, 1)
    bal_amt = bal.amount if bal else 0.0
    bal_ts = bal.timestamp if bal and bal.timestamp else datetime.combine(date.today(), datetime.min.time())

    if plan_start is None or plan_end is None:
        earliest_tx = session.query(Transaction).order_by(Transaction.timestamp).first()
        earliest_date = earliest_tx.timestamp.date() if earliest_tx else bal_ts.date()
        plan_start = min(earliest_date, bal_ts.date())
        plan_end = date.today() + timedelta(days=3650)  # ~10 years

    # real transactions
    txns = session.query(Transaction).order_by(Transaction.timestamp).all()

    # irregular forecast within planning window
    irr_start = max(date.today(), bal_ts.date())
    irr_forecast = irregular_daily_series(
        session,
        irr_start,
        plan_end,
        mode=IRREG_MODE,
        quantile=IRREG_QUANTILE,
    )
    irr_series: list[Transaction] = []
    for d, amt in irr_forecast:
        if amt:
            irr_series.append(
                Transaction(
                    description="Irregular",
                    amount=-amt,
                    timestamp=datetime.combine(d, datetime.min.time()),
                )
            )

    # synthetic recurring transactions across horizon
    recs = session.query(Recurring).all()
    synthetic_txns: list[Transaction] = []
    for r in recs:
        anchor = r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
        occs = occurrences_between(anchor, r.frequency, plan_start, plan_end)
        for occ in occs:
            synthetic_txns.append(
                Transaction(
                    description=r.description,
                    amount=r.amount,
                    timestamp=datetime.combine(occ, datetime.min.time()),
                    account_id=r.account_id,
                )
            )

    txns.extend(irr_series)
    txns.extend(synthetic_txns)

    for t in synthetic_txns:
        setattr(t, "_source_type", "recurring")
    for t in irr_series:
        setattr(t, "_source_type", "irregular")

    def classify_priority(t):
        src = getattr(t, "_source_type", "posted")  # posted|recurring|irregular
        amt = t.amount or 0.0
        if src == "irregular":
            return (50, 0)
        if src == "recurring":
            return (20, 0) if amt > 0 else (30, 0)
        return (20, 0) if amt > 0 else (40, 0)

    txns.sort(key=lambda t: (t.timestamp.date(), classify_priority(t)[0], classify_priority(t)[1], t.timestamp))
    for idx, t in enumerate(txns):
        t.timestamp = t.timestamp + timedelta(microseconds=idx)

    # compute offset so running balance matches stored balance at bal_ts
    total_before = 0.0
    for t in txns:
        if t.timestamp <= bal_ts:
            total_before += t.amount
    offset = bal_amt - total_before

    running = 0.0
    for t in txns:
        if not (plan_start <= t.timestamp.date() <= plan_end):
            continue
        running += t.amount
        yield LedgerRow(t.timestamp, t.description, t.amount, running + offset)


def ledger_curses(stdscr, initial_row, get_prev, get_next, bal_amt):
    rows = [initial_row]
    index = 0

    with temp_cursor(0), keypad_mode(stdscr):
        desc_w = len(initial_row.description)
        amt_w = len(f"{initial_row.amount:.2f}")
        run_w = len(f"{initial_row.running:.2f}")
        mode_label = (
            "Deterministic"
            if IRREG_MODE == "deterministic"
            else f"MC {IRREG_QUANTILE.upper()}"
        )
        footer_left = f"Irregular forecast: {mode_label}"

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

            pos = f"{index + 1}/{len(rows)}"
            footer_right = f"{bal_amt:.2f} {pos}"
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
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                curses.resize_term(0, 0)
                stdscr.clearok(True)
                continue
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
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(rows) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(rows) - 1
            elif key == ord("q"):
                break


def scroll_menu(
    stdscr,
    entries,
    index,
    height: int | None = None,
    header: str | None = None,
    footer_left: str | None = None,
    footer_right: str | None = None,
    allow_add: bool = False,
    allow_delete: bool = False,
    boxed: bool = False,
):
    """Display ``entries`` in a scrollable window.

    When ``boxed`` is ``True`` the list appears in a centered bordered overlay;
    otherwise it fills the available screen.
    """

    footer_l = footer_left if footer_left is not None else date.today().isoformat()
    footer_r = footer_right if footer_right is not None else ""

    max_entry_len = max((len(e) for e in entries), default=0)
    base_width = max(
        max_entry_len, len(header or ""), len(footer_l) + len(footer_r) + 1
    )

    with temp_cursor(0), keypad_mode(stdscr):
        while True:
            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            offset = 1 if header else 0
            if boxed:
                max_visible = max(1, h - 3 - offset)
                visible = min(len(entries), height or max_visible)
            else:
                visible = min(len(entries), height or (h - 1 - offset))

            pos = f"{index + 1}/{len(entries)}" if entries else "0/0"
            footer_r_text = f"{footer_r} {pos}".strip()

            if boxed:
                content_width = min(base_width, w - 4)
                total_height = visible + offset + 3
                with modal_box(stdscr, total_height, content_width + 4) as win:
                    if header:
                        head_x = max(0, (content_width - len(header)) // 2)
                        try:
                            win.addnstr(1, head_x + 2, header, content_width)
                        except curses.error:
                            pass

                    top = min(max(0, index - visible // 2), max(0, len(entries) - visible))
                    for i in range(visible):
                        line_idx = top + i
                        if line_idx >= len(entries):
                            break
                        line = entries[line_idx]
                        attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                        try:
                            win.addnstr(1 + offset + i, 2, line, content_width, attr)
                        except curses.error:
                            pass

                    try:
                        win.addnstr(total_height - 2, 2, footer_l, max(0, content_width))
                        win.addnstr(
                            total_height - 2,
                            2 + max(0, content_width - len(footer_r_text)),
                            footer_r_text,
                            len(footer_r_text),
                        )
                    except curses.error:
                        pass
                    try:
                        win.refresh()
                    except curses.error:
                        pass
                    key = win.getch()
            else:
                stdscr.erase()
                if header:
                    head_x = max(0, (w - len(header)) // 2)
                    try:
                        stdscr.addnstr(0, head_x, header, max(0, w - head_x))
                    except curses.error:
                        pass
                top = min(max(0, index - visible // 2), max(0, len(entries) - visible))
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

                try:
                    stdscr.addnstr(h - 1, 0, footer_l, max(0, w))
                    stdscr.addnstr(
                        h - 1,
                        max(0, w - len(footer_r_text)),
                        footer_r_text,
                        len(footer_r_text),
                    )
                except curses.error:
                    pass
                stdscr.refresh()
                key = stdscr.getch()

            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                curses.resize_term(0, 0)
                stdscr.clearok(True)
                continue
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(entries) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(entries) - 1
            elif key in (curses.KEY_ENTER, 10, 13):
                return index
            elif key == ord("a") and allow_add:
                return -1
            elif key == ord("d") and allow_delete:
                return ("delete", index)
            elif key == ord("q"):
                return None


def ledger_view(stdscr) -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""
    session = SessionLocal()
    bal = session.get(Balance, 1)
    bal_amt = bal.amount if bal else 0.0

    earliest_tx = session.query(Transaction).order_by(Transaction.timestamp).first()
    earliest_date = earliest_tx.timestamp.date() if earliest_tx else date.today()
    plan_start = earliest_date
    plan_end = end_of_month(date.today(), INITIAL_FORWARD_MONTHS)

    rows = list(ledger_rows(session, plan_start, plan_end))
    if not rows:
        session.close()
        return

    ts_list = [r.timestamp for r in rows]
    today_dt = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    start_idx = bisect_right(ts_list, today_dt) - 1
    if start_idx < 0:
        start_idx = 0
    initial_row = rows[start_idx]

    def rebuild():
        nonlocal rows, ts_list
        rows = list(ledger_rows(session, plan_start, plan_end))
        ts_list = [r.timestamp for r in rows]

    def get_prev(ts_before):
        nonlocal plan_start
        if (ts_before.date() - plan_start).days <= EDGE_TRIGGER_DAYS:
            plan_start = add_months(plan_start, -EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_left(ts_list, ts_before) - 1
        if idx >= 0:
            r = rows[idx]
            return r.timestamp, r.description, r.amount
        return None

    def get_next(ts_after):
        nonlocal plan_end
        if (plan_end - ts_after.date()).days <= EDGE_TRIGGER_DAYS:
            plan_end = end_of_month(plan_end, EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_right(ts_list, ts_after)
        if idx < len(rows):
            r = rows[idx]
            return r.timestamp, r.description, r.amount
        return None

    ledger_curses(stdscr, initial_row, get_prev, get_next, bal_amt)
    session.close()


def irregular_category_form(
    stdscr, name: str, window_days: int, alpha: float, safety_q: float, active: bool
):
    """Prompt for irregular category fields and return updated values."""

    name_new = text(stdscr, "Name", default=name)
    if name_new is None:
        return None
    win_str = text(stdscr, "Window days", default=str(window_days))
    if win_str is None:
        return None
    alpha_str = text(stdscr, "Alpha", default=str(alpha))
    if alpha_str is None:
        return None
    safety_str = text(stdscr, "Safety quantile", default=str(safety_q))
    if safety_str is None:
        return None
    active_str = text(stdscr, "Active (Y/N)", default="Y" if active else "N")
    if active_str is None:
        return None
    try:
        window_days_val = int(win_str)
        alpha_val = float(alpha_str)
        safety_val = float(safety_str)
    except ValueError:
        return None
    active_val = active_str.strip().lower() in ("y", "yes", "true", "1")
    return name_new, window_days_val, alpha_val, safety_val, active_val


def edit_irregular_category(
    stdscr, session, existing: IrregularCategory | None = None
) -> None:
    """Add or edit an irregular category."""

    form = irregular_category_form(
        stdscr,
        existing.name if existing else "",
        existing.window_days if existing else 120,
        existing.alpha if existing else 0.3,
        existing.safety_quantile if existing else 0.8,
        existing.active if existing else True,
    )
    if form is None:
        return
    name, window_days, alpha, safety_q, active = form
    if existing is None:
        cat = IrregularCategory(
            name=name,
            window_days=window_days,
            alpha=alpha,
            safety_quantile=safety_q,
            active=active,
        )
        session.add(cat)
    else:
        cat = session.get(IrregularCategory, existing.id)
        if cat is None:
            return
        cat.name = name
        cat.window_days = window_days
        cat.alpha = alpha
        cat.safety_quantile = safety_q
        cat.active = active
    session.commit()


def irregular_rules_menu(stdscr, category: IrregularCategory) -> None:
    """Manage rules for an irregular category."""

    session = SessionLocal()
    index = 0
    with temp_cursor(0), keypad_mode(stdscr):
        while True:
            rules = (
                session.query(IrregularRule)
                .filter(IrregularRule.category_id == category.id)
                .order_by(IrregularRule.id)
                .all()
            )
            entries = [r.pattern for r in rules]

            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            header = f"Rules for {category.name}"
            offset = 1
            visible = min(len(entries), h - 1 - offset)
            top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

            stdscr.erase()
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
            pos = f"{index + 1}/{len(entries)}" if entries else "0/0"
            footer_r = f"a:add d:del p:prev {pos}".strip()
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
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                curses.resize_term(0, 0)
                stdscr.clearok(True)
                continue
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(entries) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(entries) - 1
            elif key in (ord("a"), ord("A")):
                pattern = text(stdscr, "Pattern")
                if pattern:
                    session.add(
                        IrregularRule(category_id=category.id, pattern=pattern, active=True)
                    )
                    session.commit()
            elif key in (ord("d"), ord("D")) and rules:
                rule = rules[index]
                if confirm(stdscr, "Delete this rule?"):
                    session.delete(rule)
                    session.commit()
                    index = max(0, index - 1)
            elif key in (ord("p"), ord("P")) and rules:
                pattern = rules[index].pattern
                end = datetime.utcnow()
                start = end - timedelta(days=90)
                txns = (
                    session.query(Transaction)
                    .filter(Transaction.timestamp >= start, Transaction.timestamp <= end)
                    .order_by(Transaction.timestamp.desc())
                    .all()
                )
                matches = [
                    t.description
                    for t in txns
                    if pattern.lower() in t.description.lower()
                ]
                if matches:
                    scroll_menu(
                        stdscr,
                        matches,
                        0,
                        header=f"Matches for '{pattern}'",
                        boxed=True,
                    )
                else:
                    toast(stdscr, "No matches")
            elif key in (ord("q"), ord("Q"), 27):
                break
    session.close()


def irregular_menu(stdscr) -> None:
    """Manage irregular spending categories."""

    global IRREG_MODE, IRREG_QUANTILE
    session = SessionLocal()
    index = 0
    with temp_cursor(0), keypad_mode(stdscr):
        while True:
            cats = categories(session)
            name_w = max((len(c.name) for c in cats), default=0)
            entries = [
                f"{c.name:<{name_w}} | {c.window_days:>3} | {c.alpha:.2f} | {c.safety_quantile:.2f} | {'Y' if c.active else 'N'}"
                for c in cats
            ]

            h, w = stdscr.getmaxyx()
            h = max(1, h)
            w = max(1, w)
            header = "Irregular spending"
            offset = 1
            visible = min(len(entries), h - 1 - offset)
            top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

            stdscr.erase()
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
            mode_label = (
                "Deterministic"
                if IRREG_MODE == "deterministic"
                else ("MC P50" if IRREG_QUANTILE == "p50" else "MC P80")
            )
            pos = f"{index + 1}/{len(entries)}" if entries else "0/0"
            footer_r = f"{mode_label} {pos}".strip()
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
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                curses.resize_term(0, 0)
                stdscr.clearok(True)
                continue
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(entries) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(entries) - 1
            elif key in (ord("a"), ord("A")):
                edit_irregular_category(stdscr, session, None)
                session.close()
                session = SessionLocal()
            elif key in (ord("e"), ord("E")) and cats:
                edit_irregular_category(stdscr, session, cats[index])
                session.close()
                session = SessionLocal()
            elif key in (ord("r"), ord("R")) and cats:
                irregular_rules_menu(stdscr, cats[index])
            elif key in (ord("l"), ord("L")) and cats:
                days_str = text(stdscr, "Days to look back", default="120")
                if days_str is not None:
                    try:
                        days = int(days_str)
                    except ValueError:
                        days = 0
                    if days > 0:
                        end = date.today()
                        start = end - timedelta(days=days)
                        state = learn_irregular_state(session, cats[index].id, start, end)
                        avg = state.avg_gap_days or 0.0
                        med = state.median_amount or 0.0
                        toast(stdscr, f"{avg:.1f}d gap, {med:.2f} amt")
                        session.close()
                        session = SessionLocal()
            elif key in (ord("t"), ord("T")):
                if IRREG_MODE == "deterministic":
                    IRREG_MODE = "monte_carlo"
                    IRREG_QUANTILE = "p50"
                elif IRREG_QUANTILE == "p50":
                    IRREG_QUANTILE = "p80"
                else:
                    IRREG_MODE = "deterministic"
                    IRREG_QUANTILE = "p80"
            elif key in (ord("q"), ord("Q"), 27):
                break
    session.close()


def goals_curses(stdscr, entries, index, header=None, footer_right=""):
    """Display goals list with controls for add, delete, edit, and toggle."""

    with temp_cursor(0), keypad_mode(stdscr):
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
            pos = f"{index + 1}/{len(entries)}" if entries else "0/0"
            footer_r = f"{footer_right} {pos}".strip()
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
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                curses.resize_term(0, 0)
                stdscr.clearok(True)
                continue
            if key == curses.KEY_UP and index > 0:
                index -= 1
            elif key == curses.KEY_DOWN and index < len(entries) - 1:
                index += 1
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(entries) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(entries) - 1
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


def wants_goals_menu(stdscr) -> None:
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
            stdscr,
            entries,
            index,
            header="Goals (Enter=edit, a=add, d=del, t=toggle)",
            footer_right=f"{bal_amt:.2f}",
        )
        if action == "add":
            session.close()
            add_goal(stdscr)
            session = SessionLocal()
            index = len(goals)
        elif action == "edit" and goals:
            g = goals[idx]
            session.close()
            add_goal(stdscr, g)
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


def main(stdscr) -> None:
    with temp_cursor(0), keypad_mode(stdscr):
        try:
            curses.use_default_colors()
        except curses.error:  # pragma: no cover - terminals without color
            pass
        init_db()
        while True:
            choice = select(
                stdscr,
                "Select an option",
                choices=[
                    "List transactions",
                    "Edit bills",
                    "Edit income",
                    "Irregular spending",
                    "Ledger",
                    "Set balance",
                    "Wants/Goals",
                    "Settings/Help",
                    "Quit",
                ],
                boxed=False,
            )
            if choice == "List transactions":
                list_transactions(stdscr)
            elif choice == "Edit bills":
                edit_recurring(stdscr, False)
            elif choice == "Edit income":
                edit_recurring(stdscr, True)
            elif choice == "Irregular spending":
                irregular_menu(stdscr)
            elif choice == "Ledger":
                ledger_view(stdscr)
            elif choice == "Set balance":
                set_balance(stdscr)
            elif choice == "Wants/Goals":
                wants_goals_menu(stdscr)
            elif choice == "Settings/Help":
                settings_help_menu(stdscr)
            else:
                break


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    curses.wrapper(main)
