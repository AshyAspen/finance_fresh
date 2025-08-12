# Budget CLI

A simple command-line budgeting tool that records and lists incomes, bills, balance points, irregular spending categories, goals, and transactions.

## Setup

```bash
pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Usage

Run the interactive menu:

```bash
python -m budget
```

Use the arrow keys to move between sections and press Enter to open a list. Within each list, press `a` to add, `e` to edit, `d` to delete, and `q` to return. All data is stored in a local SQLite database (`transactions.db`).
