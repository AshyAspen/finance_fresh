import pytest  # noqa: F401

from tests import helpers  # noqa: F401  # ensures project root on sys.path
from budget import cli


def test_select_uses_scroll_menu(monkeypatch):
    captured = {}

    def fake_scroll(stdscr, entries, index, header=None, **kwargs):
        captured["entries"] = entries
        captured["index"] = index
        captured["header"] = header
        captured["boxed"] = kwargs.get("boxed")
        return 1  # choose second item

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())

    result = cli.select(object(), "Pick", ["A", ("B title", "b"), "C"], default="b")

    assert captured["entries"] == ["A", "B title", "C"]
    assert captured["index"] == 1
    assert captured["header"] == "Pick"
    assert captured["boxed"] is True
    assert result == "b"


def test_text_prompt_curses(monkeypatch):
    responses = [b"hello", b""]

    class FakeStdScr:
        def getmaxyx(self):
            return (24, 80)

        def keypad(self, flag):
            pass

    class FakeWin:
        instances = []

        def __init__(self):
            self.calls = []
            FakeWin.instances.append(self)

        def box(self):
            pass

        def addnstr(self, *args, **kwargs):
            pass

        def refresh(self):
            self.calls.append("refresh")

        def getstr(self, y, x, n):
            self.calls.append("getstr")
            return responses.pop(0)

        def keypad(self, flag):
            pass

    def fake_newwin(h, w, y, x):
        return FakeWin()

    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "echo", lambda: None)
    monkeypatch.setattr(cli.curses, "noecho", lambda: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)

    stdscr = FakeStdScr()
    assert cli.text(stdscr, "Prompt") == "hello"
    assert FakeWin.instances[0].calls == ["refresh", "getstr"]
    assert cli.text(stdscr, "Prompt", default="dflt") == "dflt"
    assert FakeWin.instances[1].calls == ["refresh", "getstr"]


def test_confirm_prompt_curses(monkeypatch):
    keys = [10, ord("x")]

    class FakeStdScr:
        def getmaxyx(self):
            return (24, 80)

        def keypad(self, flag):
            pass

    class FakeWin:
        instances = []

        def __init__(self):
            self.calls = []
            FakeWin.instances.append(self)

        def box(self):
            pass

        def addnstr(self, *args, **kwargs):
            pass

        def refresh(self):
            self.calls.append("refresh")

        def getch(self):
            self.calls.append("getch")
            return keys.pop(0)

        def keypad(self, flag):
            pass

    def fake_newwin(h, w, y, x):
        return FakeWin()

    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)

    stdscr = FakeStdScr()
    assert cli.confirm(stdscr, "Sure?") is True
    assert FakeWin.instances[0].calls == ["refresh", "getch"]
    assert cli.confirm(stdscr, "Sure?") is False
    assert FakeWin.instances[1].calls == ["refresh", "getch"]


def test_scroll_menu_handles_curses_error(monkeypatch):
    class FakeWin:
        def getmaxyx(self):
            return (0, 0)

        def addnstr(self, *args, **kwargs):
            raise cli.curses.error

        def addstr(self, *args, **kwargs):
            raise cli.curses.error

        def erase(self):
            pass

        def refresh(self):
            pass

        def keypad(self, flag):
            pass

        def getch(self):
            return 10  # Enter to select

        def box(self):
            pass

    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)

    index = cli.scroll_menu(FakeWin(), ["A", "B"], 0, header="hdr")
    assert index == 0


def test_scroll_menu_quits_on_q(monkeypatch):
    class FakeWin:
        def getmaxyx(self):
            return (24, 80)

        def addnstr(self, *args, **kwargs):
            pass

        def addstr(self, *args, **kwargs):
            pass

        def erase(self):
            pass

        def refresh(self):
            pass

        def keypad(self, flag):
            pass

        def getch(self):
            return ord("q")

        def box(self):
            pass

    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)

    index = cli.scroll_menu(FakeWin(), ["A", "B"], 0)
    assert index is None


def test_boxed_scroll_menu_respects_arrow_keys(monkeypatch):
    """Ensure boxed scroll menus handle arrow navigation via keypad."""

    captured = {}

    class FakeStdScr:
        def getmaxyx(self):
            return (24, 80)

        def keypad(self, flag):
            pass

    class FakeWin:
        def __init__(self):
            self.keypad_calls = []
            self.keys = [cli.curses.KEY_DOWN, 10]
            self.calls = []

        def box(self):
            pass

        def addnstr(self, *args, **kwargs):
            pass

        def refresh(self):
            self.calls.append("refresh")

        def keypad(self, flag):
            self.keypad_calls.append(flag)

        def getch(self):
            self.calls.append("getch")
            return self.keys.pop(0) if self.keys else 10

        def erase(self):
            pass

        def noutrefresh(self):
            pass

    fake_win = FakeWin()

    def fake_newwin(h, w, y, x):
        captured["win"] = fake_win
        return fake_win

    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)

    index = cli.scroll_menu(FakeStdScr(), ["A", "B"], 0, boxed=True)
    assert index == 1
    assert captured["win"].calls == ["refresh", "getch", "refresh", "getch"]
    assert captured["win"].keypad_calls[0] is True
    assert captured["win"].keypad_calls[-1] is False


def test_select_returns_none_on_quit(monkeypatch):
    def fake_scroll(stdscr, entries, index, header=None, **kwargs):
        return None

    monkeypatch.setattr(cli, "scroll_menu", fake_scroll)

    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())

    result = cli.select(object(), "Pick", ["A", "B"])
    assert result is None


def test_main_menu_not_boxed(monkeypatch):
    captured = {}

    def fake_select(stdscr, message, choices, default=None, boxed=True):
        captured["boxed"] = boxed
        return "Quit"

    class FakeStdScr:
        def keypad(self, flag):
            pass

    monkeypatch.setattr(cli, "select", fake_select)
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)

    cli.main(FakeStdScr())

    assert captured.get("boxed") is False
