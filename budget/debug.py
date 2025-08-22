"""
Debug functionality - completely isolated from production code.
Only imported and used when --debug flag is active.
"""

import os
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from .database import SessionLocal, DB_FILE
from .test_data import create_test_data


# Global debug state - only set when debug mode is active
DEBUG_MODE = False
DEBUG_SETTINGS = {
    'ledger_days': 60,
    'auto_balance_creation': True, 
    'show_debug_info': True,
    'backup_created': False
}


def enable_debug_mode() -> None:
    """Enable debug mode and set up debug environment."""
    global DEBUG_MODE
    DEBUG_MODE = True
    
    # Create backup of existing database
    backup_database()
    
    # Load test data
    print("🔧 Debug mode enabled - loading test data...")
    create_test_data()
    DEBUG_SETTINGS['backup_created'] = True
    print("✅ Debug environment ready!")


def disable_debug_mode() -> None:
    """Disable debug mode and restore original database."""
    global DEBUG_MODE
    if not DEBUG_MODE:
        return
        
    if DEBUG_SETTINGS.get('backup_created', False):
        print("🔄 Restoring original database...")
        restore_database()
        print("✅ Original data restored!")
    
    DEBUG_MODE = False


def is_debug_mode() -> bool:
    """Check if currently in debug mode."""
    return DEBUG_MODE


def get_debug_settings() -> Dict[str, Any]:
    """Get current debug settings."""
    return DEBUG_SETTINGS.copy()


def update_debug_setting(key: str, value: Any) -> None:
    """Update a debug setting."""
    if key in DEBUG_SETTINGS:
        DEBUG_SETTINGS[key] = value


def backup_database() -> bool:
    """Create backup of current database."""
    try:
        if not DB_FILE.exists():
            print("ℹ️  No existing database found - starting fresh")
            return True
            
        backup_path = DB_FILE.with_suffix('.db.backup')
        shutil.copy2(DB_FILE, backup_path)
        print(f"💾 Database backed up to {backup_path}")
        return True
    except Exception as e:
        print(f"⚠️  Failed to backup database: {e}")
        return False


def restore_database() -> bool:
    """Restore database from backup."""
    try:
        backup_path = DB_FILE.with_suffix('.db.backup')
        if not backup_path.exists():
            print("ℹ️  No backup found - keeping current database")
            return True
            
        # Remove current debug database
        if DB_FILE.exists():
            DB_FILE.unlink()
            
        # Restore from backup
        shutil.copy2(backup_path, DB_FILE)
        print(f"✅ Database restored from {backup_path}")
        return True
    except Exception as e:
        print(f"⚠️  Failed to restore database: {e}")
        return False


def cleanup_debug_files() -> None:
    """Clean up debug-related files."""
    try:
        backup_path = DB_FILE.with_suffix('.db.backup')
        if backup_path.exists():
            backup_path.unlink()
            print("🧹 Debug backup files cleaned up")
    except Exception as e:
        print(f"⚠️  Failed to clean up debug files: {e}")


# Debug settings panel functions (for TUI integration)
def get_date_range_options():
    """Get available date range options for debug settings."""
    return [
        ("30 days", 30),
        ("60 days", 60), 
        ("90 days", 90),
        ("180 days", 180),
        ("365 days", 365),
        ("2 years", 730),
        ("5 years", 1825),
        ("Custom Date", -1)
    ]


def set_ledger_days(days: int) -> None:
    """Set the number of days to calculate ledger into the future."""
    DEBUG_SETTINGS['ledger_days'] = days


def get_ledger_days() -> int:
    """Get current ledger calculation days."""
    return DEBUG_SETTINGS.get('ledger_days', 60)


def parse_custom_date(date_str: str) -> Optional[int]:
    """Parse custom date string and return days from today."""
    try:
        # Try different date formats
        formats = ['%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%Y/%m/%d']
        target_date = None
        
        for fmt in formats:
            try:
                target_date = datetime.strptime(date_str.strip(), fmt).date()
                break
            except ValueError:
                continue
        
        if not target_date:
            return None
            
        # Calculate days from today
        today = date.today()
        if target_date < today:
            return None  # Date is in the past
            
        days_diff = (target_date - today).days
        return days_diff
        
    except Exception:
        return None


def create_quick_balance(account_id: int, amount: float) -> bool:
    """Quick utility to create balance point for testing."""
    try:
        from .models import Balance
        
        with SessionLocal() as session:
            balance = Balance(
                amount=amount,
                timestamp=datetime.now(),
                account_id=account_id
            )
            session.add(balance)
            session.commit()
            return True
    except Exception as e:
        print(f"⚠️  Failed to create balance: {e}")
        return False


def get_debug_info() -> Dict[str, Any]:
    """Get current debug information for display."""
    info = {
        'debug_mode': DEBUG_MODE,
        'settings': DEBUG_SETTINGS.copy(),
        'db_file': str(DB_FILE),
        'backup_exists': DB_FILE.with_suffix('.db.backup').exists()
    }
    
    # Add database stats if in debug mode
    if DEBUG_MODE:
        try:
            from .models import Account, Transaction, Recurring, Balance
            
            with SessionLocal() as session:
                info['stats'] = {
                    'accounts': session.query(Account).count(),
                    'transactions': session.query(Transaction).count(), 
                    'recurring': session.query(Recurring).count(),
                    'balance_points': session.query(Balance).count()
                }
        except Exception:
            info['stats'] = {'error': 'Could not retrieve stats'}
    
    return info