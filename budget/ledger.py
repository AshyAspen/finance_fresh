from datetime import datetime, date, timedelta
from typing import Dict, List, NamedTuple, Optional, Tuple
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from .models import Transaction, Recurring, Balance, Account


class LedgerEntry(NamedTuple):
    date: date
    description: str
    amount: float
    balance: float
    account_id: int
    transaction_type: str  # 'transaction', 'recurring', 'balance_point'


# Predefined recurring frequency options: (display_name, internal_key)
RECURRING_FREQUENCIES = [
    ("Daily", "daily"),
    ("Weekly", "weekly"),
    ("Bi-weekly (every 2 weeks)", "biweekly"),
    ("Monthly", "monthly"),
    ("Quarterly (every 3 months)", "quarterly"),
    ("Semi-annually (every 6 months)", "semiannually"),
    ("Yearly", "yearly"),
]


def get_frequency_display_name(frequency_key: str) -> str:
    """Get display name for a frequency key."""
    for display_name, key in RECURRING_FREQUENCIES:
        if key == frequency_key:
            return display_name
    return frequency_key


def _get_month_end_date(year: int, month: int) -> int:
    """Get the last day of the given month/year."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _generate_recurring_dates(start_date: date, frequency_key: str, end_date: date) -> List[date]:
    """Generate dates for recurring transactions using proper calendar logic."""
    dates = []
    
    if frequency_key == 'daily':
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
    
    elif frequency_key == 'weekly':
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(weeks=1)
    
    elif frequency_key == 'biweekly':
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(weeks=2)
    
    elif frequency_key == 'monthly':
        current = start_date
        original_day = start_date.day
        
        while current <= end_date:
            dates.append(current)
            
            # Move to next month
            if current.month == 12:
                next_year = current.year + 1
                next_month = 1
            else:
                next_year = current.year
                next_month = current.month + 1
            
            # Try to use the original day, but handle month-end cases
            month_end = _get_month_end_date(next_year, next_month)
            if original_day <= month_end:
                current = date(next_year, next_month, original_day)
            else:
                # Use last day of month if original day doesn't exist
                current = date(next_year, next_month, month_end)
    
    elif frequency_key == 'quarterly':
        current = start_date
        original_day = start_date.day
        
        while current <= end_date:
            dates.append(current)
            
            # Move to next quarter (3 months)
            if current.month <= 9:
                next_year = current.year
                next_month = current.month + 3
            else:
                next_year = current.year + 1
                next_month = current.month + 3 - 12
            
            # Try to use the original day, but handle month-end cases
            month_end = _get_month_end_date(next_year, next_month)
            if original_day <= month_end:
                current = date(next_year, next_month, original_day)
            else:
                # Use last day of month if original day doesn't exist
                current = date(next_year, next_month, month_end)
    
    elif frequency_key == 'semiannually':
        current = start_date
        original_day = start_date.day
        
        while current <= end_date:
            dates.append(current)
            
            # Move to next semi-annual period (6 months)
            if current.month <= 6:
                next_year = current.year
                next_month = current.month + 6
            else:
                next_year = current.year + 1
                next_month = current.month + 6 - 12
            
            # Try to use the original day, but handle month-end cases
            month_end = _get_month_end_date(next_year, next_month)
            if original_day <= month_end:
                current = date(next_year, next_month, original_day)
            else:
                # Use last day of month if original day doesn't exist
                current = date(next_year, next_month, month_end)
    
    elif frequency_key == 'yearly':
        current = start_date
        while current <= end_date:
            dates.append(current)
            # Handle leap year edge case for Feb 29
            if start_date.month == 2 and start_date.day == 29:
                next_year = current.year + 1
                try:
                    current = date(next_year, 2, 29)
                except ValueError:
                    # Not a leap year, use Feb 28
                    current = date(next_year, 2, 28)
            else:
                current += relativedelta(years=1)
    
    return dates


def _generate_recurring_transactions(recurring: Recurring, start_date: date, end_date: date) -> List[Transaction]:
    """Generate transaction instances for a recurring item within date range."""
    transactions = []
    
    # Convert recurring start_date to date if it's datetime
    recurring_start = recurring.start_date.date() if isinstance(recurring.start_date, datetime) else recurring.start_date
    
    # Generate all occurrence dates
    occurrence_dates = _generate_recurring_dates(recurring_start, recurring.frequency, end_date)
    
    # Filter to only dates within our requested range
    for occurrence_date in occurrence_dates:
        if start_date <= occurrence_date <= end_date:
            # Create a virtual transaction for this occurrence
            tx = Transaction(
                description=f"{recurring.description} (recurring)",
                amount=recurring.amount,
                timestamp=datetime.combine(occurrence_date, datetime.min.time()),
                account_id=recurring.account_id,
                origin_type='recurring',
                origin_id=recurring.id,
                origin_occurrence_date=occurrence_date
            )
            transactions.append(tx)
    
    return transactions


def calculate_ledger(session: Session, account_id: int, start_date: date, end_date: date, initial_balance: float) -> List[LedgerEntry]:
    """Calculate ledger with running balances for an account, showing only days with transactions."""
    
    # Get all one-time transactions in the date range
    transactions = session.query(Transaction).filter(
        Transaction.account_id == account_id,
        Transaction.timestamp >= datetime.combine(start_date, datetime.min.time()),
        Transaction.timestamp <= datetime.combine(end_date, datetime.max.time())
    ).order_by(Transaction.timestamp).all()
    
    # Get all recurring transactions and generate instances
    recurring_items = session.query(Recurring).filter(
        Recurring.account_id == account_id
    ).all()
    
    recurring_transactions = []
    for recurring in recurring_items:
        recurring_transactions.extend(_generate_recurring_transactions(recurring, start_date, end_date))
    
    # Combine all transactions and sort by date
    all_transactions = transactions + recurring_transactions
    all_transactions.sort(key=lambda x: x.timestamp)
    
    # Group transactions by date
    daily_transactions: Dict[date, List[Transaction]] = defaultdict(list)
    for tx in all_transactions:
        tx_date = tx.timestamp.date() if isinstance(tx.timestamp, datetime) else tx.timestamp
        daily_transactions[tx_date].append(tx)
    
    # Calculate daily balances - only for days with transactions
    ledger_entries = []
    current_balance = initial_balance
    
    for tx_date in sorted(daily_transactions.keys()):
        day_transactions = daily_transactions[tx_date]
        
        # Process all transactions for this day
        for tx in day_transactions:
            current_balance += tx.amount
            tx_type = 'recurring' if hasattr(tx, 'origin_type') and tx.origin_type == 'recurring' else 'transaction'
            
            # Add individual transaction entry
            ledger_entries.append(LedgerEntry(
                date=tx_date,
                description=tx.description,
                amount=tx.amount,
                balance=current_balance,
                account_id=account_id,
                transaction_type=tx_type
            ))
    
    return ledger_entries


def get_starting_balance(session: Session, account_id: int) -> Tuple[Optional[float], Optional[date]]:
    """Get starting balance for ledger. Returns (balance, as_of_date) or (None, None) if user input needed."""
    # Find the most recent balance point for this account
    balance_point = session.query(Balance).filter(
        Balance.account_id == account_id
    ).order_by(Balance.timestamp.desc()).first()
    
    if balance_point:
        balance_date = balance_point.timestamp.date()
        # Check if balance point is within last 14 days
        if (date.today() - balance_date).days <= 14:
            # Calculate balance including transactions since balance point
            current_balance = balance_point.amount
            interim_transactions = session.query(Transaction).filter(
                Transaction.account_id == account_id,
                Transaction.timestamp > balance_point.timestamp
            ).all()
            for tx in interim_transactions:
                current_balance += tx.amount
            
            return current_balance, balance_date
    
    # No recent balance point found - user input needed
    return None, None


def get_default_ledger_date_range(session: Session, account_id: int, balance_date: date) -> Tuple[date, date]:
    """Get default date range for ledger: from balance date to configured days from today."""
    # Check if debug mode is active and use its settings
    days_ahead = 60  # Default
    
    try:
        from . import debug
        if debug.is_debug_mode():
            days_ahead = debug.get_ledger_days()
    except ImportError:
        pass  # Debug module not available, use default
    
    end_date = date.today() + timedelta(days=days_ahead)
    return balance_date, end_date


def get_account_ledger(session: Session, account_id: Optional[int] = None, 
                      start_date: Optional[date] = None, end_date: Optional[date] = None,
                      initial_balance: Optional[float] = None) -> List[LedgerEntry]:
    """Get ledger entries for an account with optional date range and balance."""
    
    # Default to the first account if none specified
    if account_id is None:
        account = session.query(Account).first()
        if not account:
            return []
        account_id = account.id
    
    # Get starting balance if not provided
    if initial_balance is None:
        balance, balance_date = get_starting_balance(session, account_id)
        if balance is None:
            # Need user input for balance
            return []
        initial_balance = balance
    else:
        # If balance was provided, we need to determine the balance date
        # Look for the most recent balance point
        balance_point = session.query(Balance).filter(
            Balance.account_id == account_id
        ).order_by(Balance.timestamp.desc()).first()
        
        if balance_point:
            balance_date = balance_point.timestamp.date()
        else:
            # No balance point found, use today as starting point
            balance_date = date.today()
    
    # Use default date range if not specified
    if start_date is None or end_date is None:
        start_date, end_date = get_default_ledger_date_range(session, account_id, balance_date)
    
    return calculate_ledger(session, account_id, start_date, end_date, initial_balance)