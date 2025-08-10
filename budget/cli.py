"""Command-line interface for budget app."""

from __future__ import annotations

from datetime import datetime, date, timedelta, time
import curses
import calendar
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
from curses import panel
from contextlib import contextmanager
from collections import defaultdict

from .database import SessionLocal, init_db, ensure_default_account
from sqlalchemy.exc import OperationalError
from .models import (
    Transaction,
    Balance,
    Recurring,
    Goal,
    Account,
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
from .services import (
    occurrences_between,
    create_transaction,
    create_transfer,
    delete_transfer,
    update_transfer,
    max_safe_payment_today,
    create_recurring_transfer,
    update_recurring_transfer,
    delete_recurring_transfer,
    earliest_posted_tx_date,
    get_first_balance,
    get_last_checkpoint,
    set_checkpoint,
    materialize_recurring_in_window,
    _posted_index_for_window,
    _overlaps_posted,
)

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

CURRENT_ACCOUNT_IDS: list[int] | None = None  # None means "All Accounts"


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
        footer_right = ""
        if CURRENT_ACCOUNT_IDS is None:
            accts = list_accounts(s)
        else:
            accts = (
                s.query(Account)
                .filter(Account.id.in_(CURRENT_ACCOUNT_IDS))
                .order_by(Account.name)
                .all()
            )
        if accts:
            if len(accts) == 1:
                acct = accts[0]
                bal_row = (
                    s.query(Balance)
                    .filter(Balance.account_id == acct.id)
                    .order_by(Balance.timestamp.desc())
                    .first()
                )
                amt = bal_row.amount if bal_row else 0.0
                ts = (
                    bal_row.timestamp.strftime("%Y-%m-%d")
                    if bal_row and bal_row.timestamp
                    else "n/a"
                )
                footer_right = f"{acct.name}: {amt:.2f} @ {ts}"
            else:
                parts: list[str] = []
                for acct in accts:
                    bal_row = (
                        s.query(Balance)
                        .filter(Balance.account_id == acct.id)
                        .order_by(Balance.timestamp.desc())
                        .first()
                    )
                    amt = bal_row.amount if bal_row else 0.0
                    parts.append(f"{acct.name}:{amt:.2f}")
                footer_right = "; ".join(parts)

    selected = scroll_menu(
        stdscr,
        titles,
        default_idx,
        header=message,
        footer_right=footer_right,
        boxed=boxed,
    )
    if selected is None:
        return None
    return values[selected]


def list_accounts(session, include_archived: bool = False):
    query = session.query(Account)
    if not include_archived:
        query = query.filter(Account.archived == False)
    return query.order_by(Account.name).all()


def account_has_activity(session, account_id: int) -> bool:
    return (
        session.query(Transaction).filter_by(account_id=account_id).first() is not None
        or session.query(Recurring).filter_by(account_id=account_id).first() is not None
        or session.query(Balance).filter_by(account_id=account_id).first() is not None
    )


def format_account_row(account: Account) -> str:
    row = f"{account.name}  \u2022  {account.type}  \u2022  {account.currency}"
    if getattr(account, "archived", False):
        row += " (archived)"
    return row


def pick_account(stdscr, session, prompt="Select account", default=None):
    accts = list_accounts(session)
    if not accts:
        return None
    choice = select(stdscr, prompt, [(a.name, a) for a in accts], default=default)
    return choice


def account_form(
    stdscr,
    name: str,
    acc_type: str,
    currency: str,
    allow_archive: bool = False,
    archived: bool = False,
):
    default = "name"
    while True:
        choices = [
            (f"Name: {name}", "name"),
            (f"Type: {acc_type}", "type"),
            (f"Currency: {currency}", "currency"),
        ]
        if allow_archive:
            choices.append((f"Archived: {'yes' if archived else 'no'}", "archived"))
        choices.extend([("Save", "save"), ("Cancel", "cancel")])
        choice = select(
            stdscr,
            "Select field to edit",
            choices,
            default=default,
        )
        if choice == "name":
            new_name = text(stdscr, "Name", default=name)
            if new_name is not None:
                name = new_name
            default = "type"
        elif choice == "type":
            picked = select(
                stdscr,
                "Type",
                ["checking", "savings", "credit_card", "loan", "cash"],
                default=acc_type,
            )
            if picked is not None:
                acc_type = picked
        elif choice == "currency":
            cur = text(stdscr, "Currency", default=currency)
            if cur is not None:
                currency = cur.upper()
            default = "save"
        elif choice == "archived":
            archived = not archived
        elif choice == "save":
            return name, acc_type, currency, archived
        else:
            return None


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


def show_key_help(stdscr, bindings):
    """Display a modal listing the provided key bindings."""

    lines = list(bindings)
    if "k: show this help" not in lines:
        lines.append("k: show this help")
    height = len(lines) + 2
    width = max(len(line) for line in lines) + 4
    with modal_box(stdscr, height, width) as win:
        for idx, line in enumerate(lines, start=1):
            try:
                win.addnstr(idx, 2, line, width - 4)
            except curses.error:
                pass
        try:
            win.refresh()
        except curses.error:
            pass
        win.getch()


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
        while True:
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
            if ch == ord("k"):
                show_key_help(stdscr, ["Enter: confirm", "any other key: cancel"])
                continue
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
    stdscr,
    session,
    from_acct: Account,
    description: str,
    timestamp: datetime,
    amount: float,
    to_acct: Account | None = None,
):
    """Interactive form for adding/editing transactions with optional transfer.

    Returns ``(description, timestamp, amount, to_acct)`` if saved, otherwise ``None``.
    """

    default = "name"
    while True:
        to_label = to_acct.name if to_acct else "Outgoing transaction"
        choice = select(
            stdscr,
            "Select field to edit",
            choices=[
                (f"From: {from_acct.name}", "from"),
                (f"Date: {timestamp.strftime('%Y-%m-%d')}", "date"),
                (f"Name: {description}", "name"),
                (f"Amount: {amount}", "amount"),
                (f"To: {to_label}", "to"),
                ("Save", "save"),
                ("Cancel", "cancel"),
            ],
            default=default,
        )

        if choice == "name":
            new_desc = text(stdscr, "Name", default=description)
            if new_desc is not None:
                description = new_desc
            default = "amount"
        elif choice == "date":
            date_str = text(
                stdscr, "Date (YYYY-MM-DD)", default=timestamp.strftime("%Y-%m-%d")
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
            default = "save"
        elif choice == "to":
            accts = (
                session.query(Account)
                .filter(Account.archived == False, Account.id != from_acct.id)
                .order_by(Account.name)
                .all()
            )
            if accts:
                picked = select(
                    stdscr, "To account", [(a.name, a) for a in accts], default=to_acct
                )
                to_acct = picked
        elif choice == "save":
            return description, timestamp, amount, to_acct
        elif choice == "from":
            continue
        else:
            return None


def add_transaction(stdscr) -> None:
    """Prompt user for transaction data and persist it."""
    session = SessionLocal()
    try:
        default_acct = None
        if CURRENT_ACCOUNT_IDS and len(CURRENT_ACCOUNT_IDS) == 1:
            default_acct = session.get(Account, CURRENT_ACCOUNT_IDS[0])
        from_acct = pick_account(stdscr, session, "From account", default=default_acct)
        if from_acct is None:
            return

        form = transaction_form(
            stdscr, session, from_acct, "", datetime.utcnow(), 0.0, None
        )
        if form is None:
            return
        description, timestamp, amount, to_acct = form
        if to_acct is None:
            txn = create_transaction(
                session, from_acct.id, description, amount, timestamp
            )
        else:
            create_transfer(
                session, from_acct.id, to_acct.id, amount, timestamp, description
            )
            txn = None

        if txn is not None:
            category_id = match_category_id(session, txn.description, txn.account_id)
            if category_id is not None:
                state = get_or_create_state(session, category_id)
                update_irregular_state(state, txn)
                session.commit()
                cat = session.get(IrregularCategory, category_id)
                if cat is not None:
                    avg = state.avg_gap_days if state.avg_gap_days is not None else 0.0
                    med = (
                        state.median_amount if state.median_amount is not None else 0.0
                    )
                    toast(
                        stdscr,
                        f"Updated \u2018{cat.name}\u2019: avg gap \u2192 {avg:.1f} days, median \u2192 ${med:.2f}",
                    )
    finally:
        session.close()



def max_payment_today_menu(stdscr) -> None:
    """Compute the maximum safe extra payment for today."""

    session = SessionLocal()
    accounts = (
        session.query(Account).filter(Account.archived == False).order_by(Account.name).all()
    )
    if not accounts:
        session.close()
        return

    target_name = select(stdscr, "Target account", [a.name for a in accounts])
    if target_name is None:
        session.close()
        return
    target = next(a for a in accounts if a.name == target_name)

    buf_str = text(stdscr, "Buffer per account", default="100")
    if buf_str is None:
        session.close()
        return
    try:
        buf_val = float(buf_str)
    except ValueError:
        buf_val = 0.0
    buffer_by_account = {acc.id: buf_val for acc in accounts}

    amt = max_safe_payment_today(
        session, target.id, buffer_by_account, horizon_days=120
    )
    session.close()
    toast(stdscr, f"You can safely pay ${amt:.2f} to {target.name} today.")


def accounts_menu(stdscr) -> None:
    global CURRENT_ACCOUNT_IDS
    session = SessionLocal()
    ensure_default_account(session)
    try:
        while True:
            choice = select(
                stdscr,
                "Accounts",
                [
                    "All Accounts",
                    "New Account",
                    "Rename Account",
                    "Delete Account",
                    "Select Single Account",
                    "Back",
                ],
                boxed=False,
            )
            if choice == "All Accounts":
                CURRENT_ACCOUNT_IDS = None
            elif choice == "New Account":
                name = text(stdscr, "Account name")
                if name is None:
                    continue
                acc_type = select(
                    stdscr,
                    "Account type",
                    ["checking", "savings", "credit_card", "loan"],
                )
                if acc_type is None:
                    continue
                session.add(Account(name=name, type=acc_type))
                session.commit()
            elif choice == "Rename Account":
                acct = pick_account(stdscr, session, "Rename which account")
                if acct:
                    new_name = text(stdscr, "New name", acct.name)
                    if new_name is not None:
                        acct.name = new_name
                        session.commit()
            elif choice == "Delete Account":
                acct = pick_account(stdscr, session, "Delete which account")
                if acct:
                    has_tx = (
                        session.query(Transaction)
                        .filter_by(account_id=acct.id)
                        .first()
                        is not None
                    )
                    if has_tx and not confirm(
                        stdscr, "Account has transactions; delete anyway?"
                    ):
                        continue
                    session.delete(acct)
                    session.commit()
                    if CURRENT_ACCOUNT_IDS and acct.id in CURRENT_ACCOUNT_IDS:
                        CURRENT_ACCOUNT_IDS = None
            elif choice == "Select Single Account":
                acct = pick_account(stdscr, session, "Select account")
                if acct:
                    CURRENT_ACCOUNT_IDS = [acct.id]
            else:
                break
    finally:
        session.close()


def accounts_page(stdscr):
    """Shows a scrollable list of accounts."""

    session = SessionLocal()
    index = 0
    try:
        with temp_cursor(0), keypad_mode(stdscr):
            while True:
                accounts = list_accounts(session, include_archived=True)
                entries: list[str] = []
                for acct in accounts:
                    row = format_account_row(acct)
                    bal_row = (
                        session.query(Balance)
                        .filter(Balance.account_id == acct.id)
                        .order_by(Balance.timestamp.desc())
                        .first()
                    )
                    if bal_row:
                        ts = bal_row.timestamp.strftime("%Y-%m-%d")
                        row += f"  \u2022  ${bal_row.amount:,.2f} (as of {ts})"
                    entries.append(row)

                h, w = stdscr.getmaxyx()
                header = "Accounts"
                offset = 1
                visible = min(len(entries), h - 2)
                top = min(max(0, index - visible // 2), max(0, len(entries) - visible))

                stdscr.erase()
                head_x = max(0, (w - len(header)) // 2)
                try:
                    stdscr.addnstr(0, head_x, header, max(0, w - head_x))
                except curses.error:
                    pass

                if entries:
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
                else:
                    hint = "No accounts. Press 'a' to add one."
                    x = max(0, (w - len(hint)) // 2)
                    try:
                        stdscr.addnstr(1, x, hint, w - x)
                    except curses.error:
                        pass

                footer = "a Add  e Edit  d Delete  Enter Open  q Back"
                try:
                    stdscr.addnstr(h - 1, 0, footer, w - 1)
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
                    form = account_form(stdscr, "", "checking", "USD")
                    if form is not None:
                        name, acc_type, currency, _ = form
                        session.add(Account(name=name, type=acc_type, currency=currency))
                        session.commit()
                        index = len(entries)
                elif key in (ord("e"), ord("E")) and accounts:
                    acct = accounts[index]
                    form = account_form(
                        stdscr,
                        acct.name,
                        acct.type,
                        acct.currency,
                        allow_archive=True,
                        archived=acct.archived,
                    )
                    if form is not None:
                        name, acc_type, currency, archived = form
                        acct = session.get(Account, acct.id)
                        if acct:
                            acct.name = name
                            acct.type = acc_type
                            acct.currency = currency
                            acct.archived = archived
                            session.commit()
                elif key in (ord("d"), ord("D")) and accounts:
                    acct = accounts[index]
                    if account_has_activity(session, acct.id):
                        resp = text(
                            stdscr,
                            "This account has activity. Delete anyway? Type the account name to confirm:",
                        )
                        if resp == acct.name:
                            session.query(Transaction).filter(
                                Transaction.account_id == acct.id
                            ).delete(synchronize_session=False)
                            session.query(Recurring).filter(
                                Recurring.account_id == acct.id
                            ).delete(synchronize_session=False)
                            session.query(Balance).filter(
                                Balance.account_id == acct.id
                            ).delete(synchronize_session=False)
                            session.delete(acct)
                            session.commit()
                            index = max(0, index - 1)
                        elif resp is not None and confirm(stdscr, "Archive instead?"):
                            acct.archived = True
                            session.commit()
                    else:
                        if confirm(stdscr, "Delete this account?"):
                            session.delete(acct)
                            session.commit()
                            index = max(0, index - 1)
                elif key in (curses.KEY_ENTER, 10, 13) and accounts:
                    open_account_ledger(stdscr, accounts[index].id)
                elif key == ord("k"):
                    show_key_help(
                        stdscr,
                        [
                            "Up/Down: move selection",
                            "PgUp/PgDn: page",
                            "Home/End: jump to start/end",
                            "a: add",
                            "e: edit",
                            "d: delete",
                            "Enter: open",
                            "q: back",
                        ],
                    )
                elif key in (ord("q"), ord("Q"), 27):
                    break
    finally:
        session.close()


def recurring_form(
    stdscr,
    session,
    from_acct: Account,
    description: str,
    start: datetime,
    amount: float,
    to_acct: Account | None,
    freq: str,
):
    """Interactive form for adding/editing recurring bills/incomes."""

    default = "name"
    while True:
        to_label = to_acct.name if to_acct else "Outgoing transaction"
        choice = select(
            stdscr,
            "Select field to edit",
            choices=[
                (f"From: {from_acct.name}", "from"),
                (f"Date: {start.strftime('%Y-%m-%d')}", "date"),
                (f"Name: {description}", "name"),
                (f"Amount: {amount}", "amount"),
                (f"To: {to_label}", "to"),
                (f"Recurring: {freq}", "recurring"),
                ("Save", "save"),
                ("Cancel", "cancel"),
            ],
            default=default,
        )

        if choice == "name":
            new_desc = text(stdscr, "Name", default=description)
            if new_desc is not None:
                description = new_desc
            default = "amount"
        elif choice == "date":
            date_str = text(
                stdscr, "Date (YYYY-MM-DD)", default=start.strftime("%Y-%m-%d")
            )
            if date_str is not None:
                try:
                    start = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    pass
        elif choice == "amount":
            amount_str = text(stdscr, "Amount", default=str(amount))
            if amount_str is not None:
                try:
                    amount = float(amount_str)
                except ValueError:
                    pass
            default = "recurring"
        elif choice == "to":
            accts = (
                session.query(Account)
                .filter(Account.archived == False, Account.id != from_acct.id)
                .order_by(Account.name)
                .all()
            )
            if accts:
                to_acct = select(
                    stdscr, "To account", [(a.name, a) for a in accts], default=to_acct
                )
        elif choice == "recurring":
            picked = select(
                stdscr, "Frequency", FREQUENCIES, default=freq
            )
            if picked is not None:
                freq = picked
            default = "save"
        elif choice == "save":
            return description, start, amount, to_acct, freq
        elif choice == "from":
            continue
        else:
            return None


def add_recurring(stdscr, is_income: bool, existing: Recurring | None = None) -> None:
    """Prompt user to add or edit a recurring bill or income."""

    session = SessionLocal()
    try:
        if existing is not None:
            default_acct = session.get(Account, existing.account_id)
        elif CURRENT_ACCOUNT_IDS and len(CURRENT_ACCOUNT_IDS) == 1:
            default_acct = session.get(Account, CURRENT_ACCOUNT_IDS[0])
        else:
            default_acct = None
        from_acct = pick_account(stdscr, session, "From account", default=default_acct)
        if from_acct is None:
            return
        description = existing.description if existing else ""
        start = existing.start_date if existing else datetime.utcnow()
        amount = abs(existing.amount) if existing else 0.0
        freq = existing.frequency if existing else "monthly"
        to_acct = None
        if existing and existing.transfer_id:
            other = (
                session.query(Recurring)
                .filter(Recurring.transfer_id == existing.transfer_id, Recurring.id != existing.id)
                .first()
            )
            if other:
                to_acct = session.get(Account, other.account_id)

        form = recurring_form(
            stdscr, session, from_acct, description, start, amount, to_acct, freq
        )
        if form is None:
            return
        description, start, amount, to_acct, freq = form

        if existing is None:
            if to_acct is None:
                amt = abs(amount) if is_income else -abs(amount)
                rec = Recurring(
                    description=description,
                    amount=amt,
                    start_date=start,
                    frequency=freq,
                    account_id=from_acct.id,
                )
                session.add(rec)
                session.commit()
            else:
                create_recurring_transfer(
                    session,
                    from_acct.id,
                    to_acct.id,
                    amount,
                    start,
                    freq,
                    description,
                )
        else:
            rec = session.get(Recurring, existing.id)
            if rec is None:
                return
            if rec.transfer_id:
                if to_acct is None:
                    delete_recurring_transfer(session, rec.transfer_id)
                    amt = abs(amount) if is_income else -abs(amount)
                    rec = Recurring(
                        description=description,
                        amount=amt,
                        start_date=start,
                        frequency=freq,
                        account_id=from_acct.id,
                    )
                    session.add(rec)
                    session.commit()
                else:
                    update_recurring_transfer(
                        session,
                        rec.transfer_id,
                        description,
                        amount,
                        start,
                        freq,
                        from_acct.id,
                        to_acct.id,
                    )
            else:
                if to_acct is None:
                    rec.description = description
                    rec.amount = abs(amount) if is_income else -abs(amount)
                    rec.start_date = start
                    rec.frequency = freq
                    rec.account_id = from_acct.id
                    session.commit()
                else:
                    session.delete(rec)
                    session.commit()
                    create_recurring_transfer(
                        session,
                        from_acct.id,
                        to_acct.id,
                        amount,
                        start,
                        freq,
                        description,
                    )
    finally:
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
                stdscr, "Date (YYYY-MM-DD)", default=target_date.strftime("%Y-%m-%d")
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
            anchor = (
                r.start_date.date()
                if isinstance(r.start_date, datetime)
                else r.start_date
            )
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
                    if rec.transfer_id:
                        delete_recurring_transfer(session, rec.transfer_id)
                    else:
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
    from_acct = session.get(Account, txn.account_id)
    to_acct = None
    if txn.transfer_id:
        other = (
            session.query(Transaction)
            .filter(Transaction.transfer_id == txn.transfer_id, Transaction.id != txn.id)
            .first()
        )
        if other:
            to_acct = session.get(Account, other.account_id)
    amt = abs(txn.amount) if txn.transfer_id else txn.amount
    form = transaction_form(
        stdscr, session, from_acct, txn.description, txn.timestamp, amt, to_acct
    )
    if form is None:
        return
    description, timestamp, amount, _ = form
    if txn.transfer_id:
        update_transfer(session, txn.transfer_id, description, amount, timestamp)
    else:
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
                    if txn.transfer_id:
                        delete_transfer(session, txn.transfer_id)
                    else:
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


def _show_balances(stdscr, lines: list[str]) -> None:
    if not lines:
        return
    width = max(len(line) for line in lines) + 4
    height = len(lines) + 2
    with modal_box(stdscr, height, width) as win:
        for idx, line in enumerate(lines, start=1):
            try:
                win.addnstr(idx, 2, line, width - 4)
            except curses.error:
                pass
        try:
            win.refresh()
        except curses.error:
            pass
        win.getch()


def last_reconcile_date(session, account_id: int) -> date:
    cp = get_last_checkpoint(session, account_id)
    if cp:
        return cp.as_of_date
    fb = get_first_balance(session, account_id)
    if fb:
        return fb.timestamp.date()
    e = earliest_posted_tx_date(session, account_id)
    return e or date.today()


def projected_balance_on(session, account_id: int, as_of: date) -> float:
    start_d = last_reconcile_date(session, account_id)
    rows = [
        r
        for r in ledger_rows(session, start_d, as_of, account_ids=[account_id])
        if r.timestamp.date() <= as_of
    ]
    return rows[-1].running_account if rows else 0.0


def list_transactions_since_checkpoint(
    session, account_id: int, start_d: date, end_d: date
):
    return (
        session.query(Transaction)
        .filter(Transaction.account_id == account_id)
        .filter(Transaction.timestamp >= datetime.combine(start_d, time.min))
        .filter(Transaction.timestamp <= datetime.combine(end_d, time.max))
        .order_by(Transaction.timestamp.asc())
        .all()
    )


def reconcile_screen(
    stdscr,
    session,
    account_id: int,
    start_d: date,
    end_d: date,
    entered_amount: float,
) -> bool:
    acct = session.get(Account, account_id)
    index = 0
    while True:
        txns = list_transactions_since_checkpoint(session, account_id, start_d, end_d)
        proj = projected_balance_on(session, account_id, end_d)
        diff = entered_amount - proj
        entries = [
            f"{t.timestamp.strftime('%Y-%m-%d')} {t.description} {t.amount:+.2f}"
            for t in txns
        ]
        if not entries:
            entries = ["(none)"]

        header = f"{acct.name} [{start_d}..{end_d}] entered {entered_amount:.2f}"
        footer_left = "a Add  e Edit  r Recalc  c Confirm  q Cancel"
        footer_right = f"Proj {proj:.2f} Diff {diff:+.2f}"

        with temp_cursor(0), keypad_mode(stdscr):
            h, w = stdscr.getmaxyx()
            visible = max(1, h - 2)
            top = 0
            while True:
                stdscr.erase()
                try:
                    stdscr.addnstr(0, 0, header, w - 1)
                except curses.error:
                    pass
                top = min(max(0, index - visible // 2), max(0, len(entries) - visible))
                for i in range(min(len(entries), visible)):
                    line_idx = top + i
                    line = entries[line_idx]
                    attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                    try:
                        stdscr.addnstr(1 + i, 0, line, w - 1, attr)
                    except curses.error:
                        pass
                try:
                    stdscr.addnstr(h - 1, 0, footer_left, w - 1)
                    stdscr.addnstr(
                        h - 1,
                        max(0, w - len(footer_right)),
                        footer_right,
                        len(footer_right),
                    )
                except curses.error:
                    pass
                stdscr.refresh()
                ch = stdscr.getch()
                if ch == curses.KEY_UP and index > 0:
                    index -= 1
                elif ch == curses.KEY_DOWN and index < len(entries) - 1:
                    index += 1
                elif ch == curses.KEY_PPAGE:
                    index = max(0, index - visible)
                elif ch == curses.KEY_NPAGE:
                    index = min(len(entries) - 1, index + visible)
                elif ch in (curses.KEY_ENTER, 10, 13, ord("e")):
                    if txns and index < len(txns):
                        edit_transaction(stdscr, session, txns[index])
                        session.commit()
                    break
                elif ch == ord("a"):
                    from_acct = acct
                    form = transaction_form(
                        stdscr,
                        session,
                        from_acct,
                        "",
                        datetime.combine(end_d, time.min),
                        0.0,
                        None,
                    )
                    if form is not None:
                        desc, ts, amt, to_acct = form
                        if to_acct is None:
                            create_transaction(session, from_acct.id, desc, amt, ts)
                        else:
                            create_transfer(
                                session,
                                from_acct.id,
                                to_acct.id,
                                amt,
                                ts,
                                desc,
                            )
                        session.commit()
                    break
                elif ch == ord("r"):
                    break
                elif ch == ord("c"):
                    return True
                elif ch == ord("q"):
                    return False
        # loop back to refresh after add/edit/recalc


def set_balance(stdscr) -> None:
    """Prompt the user to store their current balance."""
    session = SessionLocal()
    try:
        acct: Account | None
        if CURRENT_ACCOUNT_IDS and len(CURRENT_ACCOUNT_IDS) == 1:
            acct = session.get(Account, CURRENT_ACCOUNT_IDS[0])
        else:
            scope_ids = (
                CURRENT_ACCOUNT_IDS
                if CURRENT_ACCOUNT_IDS is not None
                else [a.id for a in list_accounts(session)]
            )
            lines: list[str] = []
            for aid in scope_ids:
                a = session.get(Account, aid)
                bal_row = (
                    session.query(Balance)
                    .filter(Balance.account_id == aid)
                    .order_by(Balance.timestamp.desc())
                    .first()
                )
                amt = bal_row.amount if bal_row else 0.0
                ts = (
                    bal_row.timestamp.strftime("%Y-%m-%d")
                    if bal_row and bal_row.timestamp
                    else "n/a"
                )
                lines.append(f"{a.name}: {amt:.2f} @ {ts}")
            _show_balances(stdscr, lines)
            acct = pick_account(stdscr, session, "Balance for account")
        if acct is None:
            return

        bal_row = (
            session.query(Balance)
            .filter(Balance.account_id == acct.id)
            .order_by(Balance.timestamp.desc())
            .first()
        )
        default_amt = f"{bal_row.amount:.2f}" if bal_row else None
        amount_str = text(stdscr, f"Balance for {acct.name}", default=default_amt)
        if amount_str is None:
            return
        try:
            amount = float(amount_str)
        except ValueError:
            return

        try:
            date_str = text(
                stdscr,
                "As of date (YYYY-MM-DD)",
                default=date.today().strftime("%Y-%m-%d"),
            )
        except StopIteration:
            date_str = None
        if date_str:
            try:
                as_of = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                as_of = date.today()
        else:
            as_of = date.today()

        first_bal = get_first_balance(session, acct.id)
        if first_bal is None:
            session.add(
                Balance(
                    amount=amount,
                    timestamp=datetime.combine(as_of, time.min),
                    account_id=acct.id,
                )
            )
            session.commit()
            set_checkpoint(session, acct.id, as_of)
            return

        start_d = last_reconcile_date(session, acct.id)
        proj = projected_balance_on(session, acct.id, as_of)
        if round(proj - amount, 2) == 0.0:
            session.add(
                Balance(
                    amount=amount,
                    timestamp=datetime.combine(as_of, time.min),
                    account_id=acct.id,
                )
            )
            session.commit()
            set_checkpoint(session, acct.id, as_of)
            return

        ok = reconcile_screen(stdscr, session, acct.id, start_d, as_of, amount)
        if not ok:
            session.rollback()
            return
        materialize_recurring_in_window(session, start_d, as_of, [acct.id])
        session.add(
            Balance(
                amount=amount,
                timestamp=datetime.combine(as_of, time.min),
                account_id=acct.id,
            )
        )
        session.commit()
        set_checkpoint(session, acct.id, as_of)
    finally:
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
            occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(
                microseconds=i
            )
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
    if next_txn is not None and (
        next_rec_time is None or next_txn.timestamp <= next_rec_time
    ):
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
            occ_dt = datetime.combine(occ, datetime.min.time()) + timedelta(
                microseconds=i
            )
            if occ_dt >= before:
                occ_prev = occurrence_on_or_before(
                    r.start_date.date(), r.frequency, before.date() - timedelta(days=1)
                )
                if occ_prev is None:
                    continue
                occ_dt = datetime.combine(occ_prev, datetime.min.time()) + timedelta(
                    microseconds=i
                )
            if occ_dt < before and (prev_rec_time is None or occ_dt > prev_rec_time):
                prev_rec_time = occ_dt
                prev_rec = r
    if prev_txn is None and prev_rec is None:
        return None
    if prev_txn is not None and (
        prev_rec_time is None or prev_txn.timestamp >= prev_rec_time
    ):
        return prev_txn.timestamp, prev_txn.description, prev_txn.amount
    return prev_rec_time, prev_rec.description, prev_rec.amount


@dataclass
class LedgerRow:
    timestamp: datetime
    account_id: int
    description: str
    amount: float
    running_account: float
    running_total: float

    @property
    def date(self) -> date:
        return self.timestamp.date()

    @property
    def running(self) -> float:
        return self.running_account


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def end_of_month(d: date, months: int = 0) -> date:
    d = add_months(d, months)
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def ledger_rows(
    session,
    plan_start: date | None = None,
    plan_end: date | None = None,
    account_ids: list[int] | None = None,
):
    if account_ids is None:
        ids = set()
        ids.update(a for (a,) in session.query(Transaction.account_id).distinct())
        ids.update(a for (a,) in session.query(Recurring.account_id).distinct())
        ids.update(a for (a,) in session.query(Balance.account_id).distinct())
        account_ids = sorted(ids)
    if not account_ids:
        account_ids = [ensure_default_account(session).id]

    first_bal_ts: dict[int, datetime] = {}
    first_bal_amt: dict[int, float] = {}
    earliest_tx_d: dict[int, date | None] = {}
    for aid in account_ids:
        fb = get_first_balance(session, aid)
        if fb:
            first_bal_ts[aid] = fb.timestamp
            first_bal_amt[aid] = fb.amount or 0.0
        else:
            first_bal_ts[aid] = datetime.combine(date.today(), time.min)
            first_bal_amt[aid] = 0.0
        earliest_tx_d[aid] = earliest_posted_tx_date(session, aid)

    min_bal_date = min(ts.date() for ts in first_bal_ts.values())

    if plan_start is None or plan_end is None:
        earliest_tx = (
            session.query(Transaction)
            .filter(Transaction.account_id.in_(account_ids))
            .order_by(Transaction.timestamp)
            .first()
        )
        earliest_date = earliest_tx.timestamp.date() if earliest_tx else min_bal_date
        plan_start = min(earliest_date, min_bal_date)
        plan_end = date.today() + timedelta(days=3650)  # ~10 years

    posted_idx = _posted_index_for_window(session, account_ids, plan_start, date.today())

    txns = (
        session.query(Transaction)
        .filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp)
        .all()
    )

    recs = session.query(Recurring).filter(Recurring.account_id.in_(account_ids)).all()
    synthetic_txns: list[Transaction] = []
    for r in recs:
        anchor = (
            r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
        )
        occs = occurrences_between(anchor, r.frequency, plan_start, plan_end)
        lower_bound = max(
            plan_start, earliest_tx_d[r.account_id] or first_bal_ts[r.account_id].date()
        )
        for occ in occs:
            if occ < lower_bound:
                continue
            synthetic_txns.append(
                Transaction(
                    description=r.description,
                    amount=r.amount,
                    timestamp=datetime.combine(occ, datetime.min.time()),
                    account_id=r.account_id,
                )
            )
    txns.extend(synthetic_txns)

    irr_start = max(date.today(), min_bal_date)
    irr_series: list[Transaction] = []
    for aid in account_ids:
        irr_forecast = irregular_daily_series(
            session,
            irr_start,
            plan_end,
            account_id=aid,
            mode=IRREG_MODE,
            quantile=IRREG_QUANTILE,
        )
        lower_bound = max(plan_start, earliest_tx_d[aid] or first_bal_ts[aid].date())
        for d, amt in irr_forecast:
            if d < lower_bound:
                continue
            if amt:
                irr_series.append(
                    Transaction(
                        description="Irregular",
                        amount=-amt,
                        timestamp=datetime.combine(d, datetime.min.time()),
                        account_id=aid,
                    )
                )
    txns.extend(irr_series)

    for t in synthetic_txns:
        setattr(t, "_source_type", "recurring")
    for t in irr_series:
        setattr(t, "_source_type", "irregular")

    def classify_priority(t):
        src = getattr(t, "_source_type", "posted")
        amt = t.amount or 0.0
        if src == "irregular":
            return (50, 0)
        if src == "recurring":
            return (20, 0) if amt > 0 else (30, 0)
        return (20, 0) if amt > 0 else (40, 0)

    def _ledger_sort_key(t: Transaction):
        prio, rank = classify_priority(t)
        date_key = t.timestamp.date()
        src_key = getattr(t, "_source_type", "posted") or "posted"
        acct_key = t.account_id if t.account_id is not None else -1
        id_key = t.id if t.id is not None else 0
        desc_key = t.description or ""
        amt_key = 0.0 if t.amount is None else float(f"{abs(t.amount):.2f}")
        return (
            date_key,
            prio,
            rank,
            src_key,
            acct_key,
            id_key,
            desc_key,
            amt_key,
            t.timestamp,
        )

    for t in txns:
        assert t.timestamp is not None
        assert isinstance(t.account_id if t.account_id is not None else -1, int)

    txns.sort(key=_ledger_sort_key)

    def _synthetic_overlaps_posted(t: Transaction) -> bool:
        return _overlaps_posted(
            posted_idx,
            t.account_id,
            t.timestamp.date(),
            t.amount or 0.0,
            t.description or "",
        )

    offset: dict[int, float] = {}
    for aid in account_ids:
        bal_ts = first_bal_ts[aid]
        bal_amt = first_bal_amt[aid]
        total_before = 0.0
        for t in txns:
            if (
                t.account_id == aid
                and t.timestamp <= bal_ts
                and getattr(t, "_source_type", "posted") == "posted"
            ):
                total_before += t.amount or 0.0
        offset[aid] = bal_amt - total_before

    running_by_acct: defaultdict[int, float] = defaultdict(float)
    last_ts: datetime | None = None
    bump = 0
    for t in txns:
        if not (plan_start <= t.timestamp.date() <= plan_end):
            continue
        aid = t.account_id
        ts_d = t.timestamp.date()
        src = getattr(t, "_source_type", "posted")
        first_ts_d = first_bal_ts[aid].date()
        if ts_d < first_ts_d:
            eff = t.amount or 0.0
        elif first_ts_d <= ts_d <= date.today():
            if src == "posted":
                eff = t.amount or 0.0
            else:
                eff = 0.0 if _synthetic_overlaps_posted(t) else (t.amount or 0.0)
        else:
            eff = t.amount or 0.0

        if t.timestamp == last_ts:
            bump += 1
        else:
            last_ts = t.timestamp
            bump = 0

        running_by_acct[aid] += eff
        display_running = running_by_acct[aid] + offset[aid]
        running_total = sum(running_by_acct.values()) + sum(offset.values())
        yield LedgerRow(
            t.timestamp + timedelta(microseconds=bump),
            t.account_id,
            t.description,
            t.amount,
            display_running,
            running_total,
        )


def ledger_curses(
    stdscr, initial_row, get_prev, get_next, bal_amt, account_names, multi
):
    global IRREG_MODE, IRREG_QUANTILE
    rows = [initial_row]
    index = 0

    with temp_cursor(0), keypad_mode(stdscr):
        if multi:
            acct_w = max(len("Account"), max(len(n) for n in account_names.values()))
            desc_w = max(len("Description"), len(initial_row.description))
            amt_w = max(len("Amount"), len(f"{initial_row.amount:.2f}"))
            run_w = max(len("Balance (Acct)"), len(f"{initial_row.running_account:.2f}"))
            tot_w = max(len("Balance (Total)"), len(f"{initial_row.running_total:.2f}"))
        else:
            desc_w = max(len("Description"), len(initial_row.description))
            amt_w = max(len("Amount"), len(f"{initial_row.amount:.2f}"))
            run_w = max(len("Balance"), len(f"{initial_row.running_account:.2f}"))
            acct_w = 0
            tot_w = 0
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
            visible = h - 2 if multi else h - 1

            while index < visible // 2:
                prev_row = get_prev(rows[0].timestamp)
                if prev_row is None:
                    break
                rows.insert(0, prev_row)
                desc_w = max(desc_w, len(prev_row.description))
                amt_w = max(amt_w, len(f"{prev_row.amount:.2f}"))
                run_w = max(run_w, len(f"{prev_row.running_account:.2f}"))
                if multi:
                    tot_w = max(tot_w, len(f"{prev_row.running_total:.2f}"))
                index += 1

            while len(rows) < visible:
                next_row = get_next(rows[-1].timestamp)
                if next_row is None:
                    break
                rows.append(next_row)
                desc_w = max(desc_w, len(next_row.description))
                amt_w = max(amt_w, len(f"{next_row.amount:.2f}"))
                run_w = max(run_w, len(f"{next_row.running_account:.2f}"))
                if multi:
                    tot_w = max(tot_w, len(f"{next_row.running_total:.2f}"))

            top = min(max(0, index - visible // 2), max(0, len(rows) - visible))

            stdscr.erase()
            if multi:
                header = (
                    f"{'Date'} | "
                    f"{'Account':<{acct_w}} | "
                    f"{'Description':<{desc_w}} | "
                    f"{'Amount':>{amt_w}} | "
                    f"{'Balance (Acct)':>{run_w}} | "
                    f"{'Balance (Total)':>{tot_w}}"
                )
                try:
                    stdscr.addnstr(0, 0, header, w - 1, curses.A_BOLD)
                except curses.error:
                    pass
                row_y_start = 1
            else:
                row_y_start = 0

            for i in range(visible):
                line_idx = top + i
                if line_idx >= len(rows):
                    break
                r = rows[line_idx]
                if multi:
                    acct = account_names.get(r.account_id, str(r.account_id))
                    line = (
                        f"{r.date.strftime('%Y-%m-%d')} | "
                        f"{acct:<{acct_w}} | "
                        f"{r.description:<{desc_w}} | "
                        f"{r.amount:>{amt_w}.2f} | "
                        f"{r.running_account:>{run_w}.2f} | "
                        f"{r.running_total:>{tot_w}.2f}"
                    )
                else:
                    line = (
                        f"{r.date.strftime('%Y-%m-%d')} | "
                        f"{r.description:<{desc_w}} | "
                        f"{r.amount:>{amt_w}.2f} | {r.running_account:>{run_w}.2f}"
                    )
                attr = curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                try:
                    stdscr.addnstr(row_y_start + i, 0, line, w - 1, attr)
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
                    prev_row = get_prev(rows[0].timestamp)
                    if prev_row is not None:
                        rows.insert(0, prev_row)
                        desc_w = max(desc_w, len(prev_row.description))
                        amt_w = max(amt_w, len(f"{prev_row.amount:.2f}"))
                        run_w = max(run_w, len(f"{prev_row.running_account:.2f}"))
                        if multi:
                            tot_w = max(tot_w, len(f"{prev_row.running_total:.2f}"))
            elif key == curses.KEY_DOWN:
                if index < len(rows) - 1:
                    index += 1
                else:
                    next_row = get_next(rows[-1].timestamp)
                    if next_row is not None:
                        rows.append(next_row)
                        desc_w = max(desc_w, len(next_row.description))
                        amt_w = max(amt_w, len(f"{next_row.amount:.2f}"))
                        run_w = max(run_w, len(f"{next_row.running_account:.2f}"))
                        if multi:
                            tot_w = max(tot_w, len(f"{next_row.running_total:.2f}"))
                        index += 1
            elif key == curses.KEY_PPAGE:
                index = max(0, index - visible)
            elif key == curses.KEY_NPAGE:
                index = min(len(rows) - 1, index + visible)
            elif key == curses.KEY_HOME:
                index = 0
            elif key == curses.KEY_END:
                index = len(rows) - 1
            elif key == ord("a"):
                add_transaction(stdscr)
                current_ts = rows[index].timestamp
                if hasattr(get_prev, "refresh"):
                    new_row = get_prev.refresh(current_ts)
                    rows = [new_row]
                    index = 0
                    desc_w = len(new_row.description)
                    amt_w = len(f"{new_row.amount:.2f}")
                    run_w = len(f"{new_row.running_account:.2f}")
                    if multi:
                        tot_w = len(f"{new_row.running_total:.2f}")
            elif key in (ord("t"), ord("T")):
                if IRREG_MODE == "deterministic":
                    IRREG_MODE = "monte_carlo"
                    IRREG_QUANTILE = "p50"
                elif IRREG_QUANTILE == "p50":
                    IRREG_QUANTILE = "p80"
                else:
                    IRREG_MODE = "deterministic"
                    IRREG_QUANTILE = "p80"
                current_ts = rows[index].timestamp
                if hasattr(get_prev, "refresh"):
                    new_row = get_prev.refresh(current_ts)
                    rows = [new_row]
                    index = 0
                    desc_w = len(new_row.description)
                    amt_w = len(f"{new_row.amount:.2f}")
                    run_w = len(f"{new_row.running_account:.2f}")
                    if multi:
                        tot_w = len(f"{new_row.running_total:.2f}")
                mode_label = (
                    "Deterministic"
                    if IRREG_MODE == "deterministic"
                    else f"MC {IRREG_QUANTILE.upper()}"
                )
                footer_left = f"Irregular forecast: {mode_label}"
            elif key == ord("k"):
                show_key_help(
                    stdscr,
                    [
                        "Up/Down: move",
                        "PgUp/PgDn: page",
                        "Home/End: jump to start/end",
                        "t: toggle forecast mode",
                        "q: quit/back",
                    ],
                )
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

                    top = min(
                        max(0, index - visible // 2), max(0, len(entries) - visible)
                    )
                    for i in range(visible):
                        line_idx = top + i
                        if line_idx >= len(entries):
                            break
                        line = entries[line_idx]
                        attr = (
                            curses.A_REVERSE if line_idx == index else curses.A_NORMAL
                        )
                        try:
                            win.addnstr(1 + offset + i, 2, line, content_width, attr)
                        except curses.error:
                            pass

                    try:
                        win.addnstr(
                            total_height - 2, 2, footer_l, max(0, content_width)
                        )
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
            elif key == ord("k"):
                bindings = [
                    "Up/Down: move selection",
                    "PgUp/PgDn: page",
                    "Home/End: jump to start/end",
                    "Enter: select",
                ]
                if allow_add:
                    bindings.append("a: add")
                if allow_delete:
                    bindings.append("d: delete")
                bindings.append("q: quit/back")
                show_key_help(stdscr, bindings)
            elif key == ord("q"):
                return None


def ledger_view(stdscr) -> None:
    """Display a scrollable ledger as ``date | name | amount | balance``."""
    session = SessionLocal()
    ensure_default_account(session)
    account_ids = CURRENT_ACCOUNT_IDS
    if account_ids is None:
        accounts = (
            session.query(Account)
            .filter(Account.archived == False)
            .order_by(Account.name)
            .all()
        )
        account_ids = [a.id for a in accounts]
    else:
        accounts = session.query(Account).filter(Account.id.in_(account_ids)).all()
    if not account_ids:
        default_acc = ensure_default_account(session)
        account_ids = [default_acc.id]
        accounts = [default_acc]
    bal_amt = 0.0
    for aid in account_ids:
        bal = (
            session.query(Balance)
            .filter(Balance.account_id == aid)
            .order_by(Balance.timestamp.desc())
            .first()
        )
        if bal:
            bal_amt += bal.amount

    earliest_tx = (
        session.query(Transaction)
        .filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp)
        .first()
    )
    earliest_date = earliest_tx.timestamp.date() if earliest_tx else date.today()
    plan_start = earliest_date
    plan_end = end_of_month(date.today(), INITIAL_FORWARD_MONTHS)
    account_names = {a.id: a.name for a in accounts}

    rows = list(ledger_rows(session, plan_start, plan_end, account_ids))
    if not rows:
        session.close()
        return

    ts_list = [(r.timestamp, i) for i, r in enumerate(rows)]
    today_date = date.today()
    start_idx = bisect_right([r.timestamp.date() for r in rows], today_date) - 1
    if start_idx < 0:
        start_idx = 0
    initial_row = rows[start_idx]

    def rebuild():
        nonlocal rows, ts_list
        rows = list(ledger_rows(session, plan_start, plan_end, account_ids))
        ts_list = [(r.timestamp, i) for i, r in enumerate(rows)]

    def refresh(ts_current: datetime):
        rebuild()
        target_date = ts_current.date()
        # Use a date list for bisection to avoid microsecond bump issues
        date_list = [r.timestamp.date() for r in rows]
        idx = bisect_right(date_list, target_date) - 1
        if idx < 0:
            idx = 0
        return rows[idx]

    def get_prev(ts_before):
        nonlocal plan_start
        if (ts_before.date() - plan_start).days <= EDGE_TRIGGER_DAYS:
            plan_start = add_months(plan_start, -EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_left(ts_list, (ts_before, -1)) - 1
        if idx >= 0:
            return rows[idx]
        return None

    def get_next(ts_after):
        nonlocal plan_end
        if (plan_end - ts_after.date()).days <= EDGE_TRIGGER_DAYS:
            plan_end = end_of_month(plan_end, EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_right(ts_list, (ts_after, float("inf")))
        if idx < len(rows):
            return rows[idx]
        return None

    get_prev.refresh = refresh  # type: ignore[attr-defined]
    ledger_curses(
        stdscr,
        initial_row,
        get_prev,
        get_next,
        bal_amt,
        account_names,
        len(account_ids) > 1,
    )
    session.close()


def open_account_ledger(stdscr, account_id: int) -> None:
    session = SessionLocal()
    account = session.get(Account, account_id)
    if account is None:
        session.close()
        return
    bal = (
        session.query(Balance)
        .filter(Balance.account_id == account_id)
        .order_by(Balance.timestamp.desc())
        .first()
    )
    bal_amt = bal.amount if bal else 0.0
    earliest_tx = (
        session.query(Transaction)
        .filter(Transaction.account_id == account_id)
        .order_by(Transaction.timestamp)
        .first()
    )
    earliest_date = earliest_tx.timestamp.date() if earliest_tx else date.today()
    plan_start = earliest_date
    plan_end = end_of_month(date.today(), INITIAL_FORWARD_MONTHS)
    account_names = {account_id: account.name}
    rows = list(ledger_rows(session, plan_start, plan_end, [account_id]))
    if not rows:
        session.close()
        return
    ts_list = [(r.timestamp, i) for i, r in enumerate(rows)]
    today_date = date.today()
    start_idx = bisect_right([r.timestamp.date() for r in rows], today_date) - 1
    if start_idx < 0:
        start_idx = 0
    initial_row = rows[start_idx]

    def rebuild():
        nonlocal rows, ts_list
        rows = list(ledger_rows(session, plan_start, plan_end, [account_id]))
        ts_list = [(r.timestamp, i) for i, r in enumerate(rows)]

    def refresh(ts_current: datetime):
        rebuild()
        target_date = ts_current.date()
        date_list = [r.timestamp.date() for r in rows]
        idx = bisect_right(date_list, target_date) - 1
        if idx < 0:
            idx = 0
        return rows[idx]

    def get_prev(ts_before):
        nonlocal plan_start
        if (ts_before.date() - plan_start).days <= EDGE_TRIGGER_DAYS:
            plan_start = add_months(plan_start, -EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_left(ts_list, (ts_before, -1)) - 1
        if idx >= 0:
            return rows[idx]
        return None

    def get_next(ts_after):
        nonlocal plan_end
        if (plan_end - ts_after.date()).days <= EDGE_TRIGGER_DAYS:
            plan_end = end_of_month(plan_end, EXTEND_CHUNK_MONTHS)
            rebuild()
        idx = bisect_right(ts_list, (ts_after, float("inf")))
        if idx < len(rows):
            return rows[idx]
        return None

    get_prev.refresh = refresh  # type: ignore[attr-defined]
    ledger_curses(
        stdscr,
        initial_row,
        get_prev,
        get_next,
        bal_amt,
        account_names,
        False,
    )
    session.close()


def irregular_category_form(
    stdscr,
    session,
    name: str,
    window_days: int,
    alpha: float,
    safety_q: float,
    active: bool,
    account: Account | None = None,
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
    if account is not None:
        default_acct = account
    elif CURRENT_ACCOUNT_IDS and len(CURRENT_ACCOUNT_IDS) == 1:
        default_acct = session.get(Account, CURRENT_ACCOUNT_IDS[0])
    else:
        default_acct = None
    acct = pick_account(stdscr, session, "Account", default=default_acct)
    if acct is None:
        return None
    try:
        window_days_val = int(win_str)
        alpha_val = float(alpha_str)
        safety_val = float(safety_str)
    except ValueError:
        return None
    active_val = active_str.strip().lower() in ("y", "yes", "true", "1")
    return (
        name_new,
        window_days_val,
        alpha_val,
        safety_val,
        active_val,
        acct.id,
    )


def edit_irregular_category(
    stdscr, session, existing: IrregularCategory | None = None
) -> None:
    """Add or edit an irregular category."""

    existing_acct = (
        session.get(Account, existing.account_id) if existing else None
    )
    form = irregular_category_form(
        stdscr,
        session,
        existing.name if existing else "",
        existing.window_days if existing else 120,
        existing.alpha if existing else 0.3,
        existing.safety_quantile if existing else 0.8,
        existing.active if existing else True,
        existing_acct,
    )
    if form is None:
        return
    name, window_days, alpha, safety_q, active, account_id = form
    if existing is None:
        cat = IrregularCategory(
            name=name,
            window_days=window_days,
            alpha=alpha,
            safety_quantile=safety_q,
            active=active,
            account_id=account_id,
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
        cat.account_id = account_id
        for rule in cat.rules:
            rule.account_id = account_id
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
            acct = session.get(Account, category.account_id)
            acct_name = acct.name if acct else ""
            header = f"Rules for {category.name} ({acct_name})"
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
                        IrregularRule(
                            category_id=category.id,
                            account_id=category.account_id,
                            pattern=pattern,
                            active=True,
                        )
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
                    .filter(
                        Transaction.timestamp >= start, Transaction.timestamp <= end
                    )
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
            elif key == ord("k"):
                show_key_help(
                    stdscr,
                    [
                        "Up/Down: move selection",
                        "PgUp/PgDn: page",
                        "Home/End: jump to start/end",
                        "a: add",
                        "d: delete",
                        "p: pattern matches",
                        "q: quit/back",
                    ],
                )
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
            acct_map = {
                a.id: a.name
                for a in session.query(Account)
                .filter(Account.id.in_([c.account_id for c in cats]))
                .all()
            }
            acct_w = max((len(acct_map.get(c.account_id, "")) for c in cats), default=0)
            entries = [
                (
                    f"{c.name:<{name_w}} | {acct_map.get(c.account_id, ''):<{acct_w}} | "
                    f"{c.window_days:>3} | {c.alpha:.2f} | {c.safety_quantile:.2f} | "
                    f"{'Y' if c.active else 'N'}"
                )
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
                        state = learn_irregular_state(
                            session, cats[index].id, start, end
                        )
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
            elif key == ord("k"):
                show_key_help(
                    stdscr,
                    [
                        "Up/Down: move selection",
                        "PgUp/PgDn: page",
                        "Home/End: jump to start/end",
                        "a: add",
                        "e: edit",
                        "r: rules",
                        "l: learn",
                        "t: toggle forecast mode",
                        "q: quit/back",
                    ],
                )
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
            elif key == ord("k"):
                show_key_help(
                    stdscr,
                    [
                        "Up/Down: move selection",
                        "PgUp/PgDn: page",
                        "Home/End: jump to start/end",
                        "Enter: edit",
                        "a: add",
                        "d: delete",
                        "t: toggle",
                        "q: quit/back",
                    ],
                )
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
        session = SessionLocal()
        try:
            try:
                ensure_default_account(session)
            except OperationalError:
                pass
        finally:
            session.close()
        while True:
            choice = select(
                stdscr,
                "Select an option",
                choices=[
                    "List transactions",
                    "Max Safe Payment (today)",
                    "Edit bills",
                    "Edit income",
                    "Irregular spending",
                    "Accounts",
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
            elif choice == "Max Safe Payment (today)":
                max_payment_today_menu(stdscr)
            elif choice == "Edit bills":
                edit_recurring(stdscr, False)
            elif choice == "Edit income":
                edit_recurring(stdscr, True)
            elif choice == "Irregular spending":
                irregular_menu(stdscr)
            elif choice == "Accounts":
                accounts_page(stdscr)
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
