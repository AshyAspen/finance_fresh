"""Entry point for running the curses-based budget UI."""

from .tui import main


def entry_point() -> None:
    main()


if __name__ == "__main__":  # pragma: no cover
    entry_point()
