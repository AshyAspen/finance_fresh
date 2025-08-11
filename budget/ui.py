from __future__ import annotations

import curses
from curses import panel
from contextlib import contextmanager

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


def info(stdscr, msg: str) -> None:
    h, w = stdscr.getmaxyx()
    box_w = min(max(len(msg) + 4, 12), max(12, w - 2))
    with modal_box(stdscr, 3, box_w) as win:
        try:
            win.addnstr(1, 2, msg[: box_w - 4], box_w - 4)
        except curses.error:
            pass
        win.getch()


def toast(stdscr, msg: str, ms: int = 900):
    h, w = stdscr.getmaxyx()
    box_w = min(max(len(msg) + 4, 12), max(12, w - 2))
    with modal_box(stdscr, 3, box_w) as win:
        try:
            win.addnstr(1, 2, msg[: box_w - 4], box_w - 4)
        except curses.error:
            pass
        curses.napms(ms)

