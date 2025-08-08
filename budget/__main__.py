"""Module entry point for running the CLI via ``python -m budget``.

Previously this module invoked :func:`budget.cli.main` directly, which
expected no arguments. After refactoring the application to run under a single
``curses`` session, :func:`main` now requires the ``stdscr`` window to be
provided. Using :func:`curses.wrapper` here ensures the correct argument is
supplied and initializes/tears down the curses session automatically.
"""

import curses

from .cli import main


def entry_point() -> None:
    """Wrap the CLI ``main`` function in a curses session."""
    curses.wrapper(main)


if __name__ == "__main__":  # pragma: no cover - manual execution entry
    entry_point()
