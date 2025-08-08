import pytest

from tests import helpers  # ensures project root on sys.path
from budget import cli


def test_select_uses_scroll_menu(monkeypatch):
    captured = {}

    def fake_scroll(entries, index, header=None, **kwargs):
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

    result = cli.select("Pick", ["A", ("B title", "b"), "C"], default="b")

    assert captured["entries"] == ["A", "B title", "C"]
    assert captured["index"] == 1
    assert captured["header"] == "Pick"
    assert captured["boxed"] is True
    assert result == "b"


def test_text_prompt_curses(monkeypatch):
    responses = [b"hello", b""]

    def fake_wrapper(func):
        class FakeStdScr:
            def getmaxyx(self):
                return (24, 80)

            def keypad(self, flag):
                pass

            def noutrefresh(self):
                pass

        return func(FakeStdScr())

    def fake_newwin(h, w, y, x):
        class FakeWin:
            def box(self):
                pass

            def addnstr(self, *args, **kwargs):
                pass

            def noutrefresh(self):
                pass

            def getstr(self, y, x, n):
                return responses.pop(0)

            def keypad(self, flag):
                pass

        return FakeWin()

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "echo", lambda: None)
    monkeypatch.setattr(cli.curses, "noecho", lambda: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)
    monkeypatch.setattr(cli.curses, "doupdate", lambda: None)

    assert cli.text("Prompt") == "hello"
    assert cli.text("Prompt", default="dflt") == "dflt"


def test_confirm_prompt_curses(monkeypatch):
    keys = [10, ord("x")]

    def fake_wrapper(func):
        class FakeStdScr:
            def getmaxyx(self):
                return (24, 80)

            def keypad(self, flag):
                pass

            def noutrefresh(self):
                pass

        return func(FakeStdScr())

    def fake_newwin(h, w, y, x):
        class FakeWin:
            def box(self):
                pass

            def addnstr(self, *args, **kwargs):
                pass

            def noutrefresh(self):
                pass

            def getch(self):
                return keys.pop(0)

            def keypad(self, flag):
                pass

        return FakeWin()

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)
    monkeypatch.setattr(cli.curses, "doupdate", lambda: None)

    assert cli.confirm("Sure?") is True
    assert cli.confirm("Sure?") is False


def test_scroll_menu_handles_curses_error(monkeypatch):
    class DummySession:
        def get(self, model, ident):
            return None

        def close(self):
            pass

    def fake_wrapper(func):
        class FakeWin:
            def getmaxyx(self):
                return (0, 0)

            def addnstr(self, *args, **kwargs):
                raise cli.curses.error

            def addstr(self, *args, **kwargs):
                raise cli.curses.error

            def erase(self):
                pass

            def noutrefresh(self):
                pass

            def keypad(self, flag):
                pass

            def getch(self):
                return 10  # Enter to select

            def box(self):
                pass

        return func(FakeWin())

    monkeypatch.setattr(cli, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", lambda *args, **kwargs: fake_wrapper(lambda w: w))
    monkeypatch.setattr(cli.curses, "doupdate", lambda: None)

    index = cli.scroll_menu(["A", "B"], 0, header="hdr")
    assert index == 0


def test_scroll_menu_quits_on_q(monkeypatch):
    def fake_wrapper(func):
        class FakeWin:
            def getmaxyx(self):
                return (24, 80)

            def addnstr(self, *args, **kwargs):
                pass

            def addstr(self, *args, **kwargs):
                pass

            def erase(self):
                pass

            def noutrefresh(self):
                pass

            def keypad(self, flag):
                pass

            def getch(self):
                return ord("q")

            def box(self):
                pass

        return func(FakeWin())

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", lambda *args, **kwargs: fake_wrapper(lambda w: w))
    monkeypatch.setattr(cli.curses, "doupdate", lambda: None)

    index = cli.scroll_menu(["A", "B"], 0)
    assert index is None


def test_boxed_scroll_menu_respects_arrow_keys(monkeypatch):
    """Ensure boxed scroll menus handle arrow navigation via keypad."""

    captured = {}

    def fake_wrapper(func):
        class FakeStdScr:
            def getmaxyx(self):
                return (24, 80)

            def keypad(self, flag):
                pass

            def noutrefresh(self):
                pass

        return func(FakeStdScr())

    class FakeWin:
        def __init__(self):
            self.keypad_enabled = False
            # Simulate KEY_DOWN followed by Enter across menu iterations
            self.keys = [cli.curses.KEY_DOWN, 10]

        def box(self):
            pass

        def addnstr(self, *args, **kwargs):
            pass

        def noutrefresh(self):
            pass

        def keypad(self, flag):
            self.keypad_enabled = flag

        def getch(self):
            return self.keys.pop(0) if self.keys else 10

    fake_win = FakeWin()

    def fake_newwin(h, w, y, x):
        captured["win"] = fake_win
        return fake_win

    monkeypatch.setattr(cli.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(cli.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(cli.curses, "newwin", fake_newwin)
    monkeypatch.setattr(cli.curses, "doupdate", lambda: None)

    index = cli.scroll_menu(["A", "B"], 0, boxed=True)
    assert index == 1
    assert captured["win"].keypad_enabled is True


def test_select_returns_none_on_quit(monkeypatch):
    def fake_scroll(entries, index, header=None, **kwargs):
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

    result = cli.select("Pick", ["A", "B"])
    assert result is None


def test_main_menu_not_boxed(monkeypatch):
    captured = {}

    def fake_select(message, choices, default=None, boxed=True):
        captured["boxed"] = boxed
        return "Quit"

    monkeypatch.setattr(cli, "select", fake_select)
    monkeypatch.setattr(cli, "init_db", lambda: None)

    cli.main()

    assert captured.get("boxed") is False
