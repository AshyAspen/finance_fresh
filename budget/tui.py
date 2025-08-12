import curses

from .database import init_db
from .models import Recurring, Balance, Goal, IrregularCategory, Transaction
from .cli import _manage

MENU_ITEMS = [
    ("Incomes", lambda: _manage(Recurring, Recurring.amount > 0)),
    ("Bills", lambda: _manage(Recurring, Recurring.amount <= 0)),
    ("Balance Points", lambda: _manage(Balance)),
    ("Irregular Spending", lambda: _manage(IrregularCategory)),
    ("Goals", lambda: _manage(Goal)),
    ("Transactions", lambda: _manage(Transaction)),
    ("Quit", None),
]


def _run_menu(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    idx = 0
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Budget Lists")
        for i, (label, _) in enumerate(MENU_ITEMS):
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(2 + i, 2, label)
            if i == idx:
                stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(MENU_ITEMS)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(MENU_ITEMS)
        elif key in (curses.KEY_ENTER, 10, 13):
            label, action = MENU_ITEMS[idx]
            if action is None:
                break
            curses.def_prog_mode()
            curses.endwin()
            try:
                action()
            finally:
                curses.reset_prog_mode()
                curses.curs_set(0)
                stdscr.keypad(True)
        elif key in (ord("q"), ord("Q")):
            break


def main() -> None:
    init_db()
    curses.wrapper(_run_menu)
