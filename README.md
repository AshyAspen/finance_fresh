# Budget CLI

A simple command-line budgeting tool that records and lists transactions.

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

Transactions are stored in a local SQLite database (`transactions.db`).
