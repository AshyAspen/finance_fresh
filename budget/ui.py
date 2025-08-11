from __future__ import annotations

import curses
from curses import panel
from contextlib import contextmanager
from datetime import date

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

