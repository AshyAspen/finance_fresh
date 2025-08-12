"""Entry point for running the budget CLI."""

from .cli import main


def entry_point() -> None:
    main()


if __name__ == "__main__":  # pragma: no cover
    entry_point()
