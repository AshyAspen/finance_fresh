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

All data is stored in a local SQLite database (`transactions.db`).
