import curses
from datetime import datetime, date
from typing import Any, List, Type, Optional

from sqlalchemy import inspect

from .database import init_db, SessionLocal
from .models import Recurring, Balance, Goal, IrregularCategory, Transaction, Account
from .ledger import RECURRING_FREQUENCIES, get_account_ledger, get_starting_balance, LedgerEntry

ModelType = Type[Any]


def _get_columns(model: ModelType) -> List[str]:
    mapper = inspect(model)
    return [c.key for c in mapper.columns]


def _parse_value(col, value: str):
    if value == "":
        return None
    pytype = col.type.python_type
    if pytype is float:
        return float(value)
    if pytype is int:
        return int(value)
    if pytype is bool:
        return value.lower() in {"y", "yes", "1", "true"}
    if pytype is datetime:
        return datetime.fromisoformat(value)
    if pytype is date:
        return date.fromisoformat(value)
    return value


def _format_value(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    if v is None:
        return ""
    return str(v)


def _prompt(stdscr, text: str) -> str:
    h, _ = stdscr.getmaxyx()
    stdscr.move(h - 3, 0)
    stdscr.clrtobot()
    stdscr.addstr(h - 3, 0, text)
    stdscr.refresh()
    curses.echo()
    try:
        result = stdscr.getstr(h - 2, 0).decode().strip()
    finally:
        curses.noecho()
    return result


def _frequency_picker(stdscr) -> str:
    """Show a menu to pick recurring frequency and return the key."""
    # Clear screen and prepare for navigation
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    curses.curs_set(0)  # Hide cursor during menu navigation
    stdscr.keypad(True)  # Enable special key detection (arrow keys)
    
    idx = 0  # Currently selected menu item index
    
    while True:
        # Clear and redraw the menu each time
        stdscr.clear()
        stdscr.addstr(0, 0, "Select Recurring Frequency:")
        
        # Display each frequency option with visual selection indicator
        for i, (display_name, key) in enumerate(RECURRING_FREQUENCIES):
            if i == idx:
                # Highlight currently selected item with reverse video (inverted colors)
                stdscr.attron(curses.A_REVERSE)
            
            # Display the frequency name with indentation
            stdscr.addstr(2 + i, 2, display_name)
            
            if i == idx:
                # Turn off highlighting for next items
                stdscr.attroff(curses.A_REVERSE)
        
        # Show navigation instructions at bottom of screen
        stdscr.addstr(h - 2, 0, "↑↓ to navigate, Enter to select, q to cancel")
        stdscr.refresh()  # Make changes visible
        
        # Wait for user input
        key = stdscr.getch()
        
        # Handle navigation keys
        if key in (curses.KEY_UP, ord("k")):
            # Move selection up (with wraparound to bottom)
            idx = (idx - 1) % len(RECURRING_FREQUENCIES)
        elif key in (curses.KEY_DOWN, ord("j")):
            # Move selection down (with wraparound to top)
            idx = (idx + 1) % len(RECURRING_FREQUENCIES)
        elif key in (curses.KEY_ENTER, 10, 13):
            # User confirmed selection - return the internal frequency key
            return RECURRING_FREQUENCIES[idx][1]
        elif key in (ord("q"), ord("Q")):
            # User cancelled - return empty string
            return ""
    
    return ""


def _display_table(stdscr, model: ModelType, items: List[Any]) -> None:
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    cols = _get_columns(model)
    widths = {c: len(c) for c in cols}
    for item in items:
        for c in cols:
            widths[c] = max(widths[c], len(_format_value(getattr(item, c))))
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    stdscr.addstr(0, 0, header[:w])
    stdscr.addstr(1, 0, sep[:w])
    for i, item in enumerate(items[: h - 5]):
        row = " | ".join(
            _format_value(getattr(item, c)).ljust(widths[c]) for c in cols
        )
        stdscr.addstr(2 + i, 0, row[:w])
    stdscr.refresh()


def _add_item(stdscr, model: ModelType) -> None:
    cols = _get_columns(model)
    with SessionLocal() as session:
        data = {}
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            
            # Special handling for frequency field in recurring transactions
            if model == Recurring and c == "frequency":
                # Show frequency picker menu instead of text input
                frequency_key = _frequency_picker(stdscr)
                if not frequency_key:  # User cancelled
                    return
                data[c] = frequency_key
            else:
                # Normal text input for all other fields
                val = _prompt(stdscr, f"{c}: ")
                data[c] = _parse_value(col, val)
        
        # Create and save the new object
        obj = model(**data)
        session.add(obj)
        session.commit()


def _edit_item(stdscr, model: ModelType) -> None:
    cols = _get_columns(model)
    item_id = _prompt(stdscr, "id to edit: ")
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if not obj:
            _prompt(stdscr, "not found - press Enter")
            return
        
        for c in cols:
            if c == "id":
                continue
            col = inspect(model).columns[c]
            cur = _format_value(getattr(obj, c))
            
            # Special handling for frequency field in recurring transactions
            if model == Recurring and c == "frequency":
                # Show current frequency value and ask if user wants to change it
                from .ledger import get_frequency_display_name
                current_display = get_frequency_display_name(cur)
                change = _prompt(stdscr, f"frequency [{current_display}] - change? (y/N): ").lower()
                
                if change == 'y':
                    # Show frequency picker menu
                    frequency_key = _frequency_picker(stdscr)
                    if frequency_key:  # User didn't cancel
                        setattr(obj, c, frequency_key)
            else:
                # Normal text input for all other fields
                val = _prompt(stdscr, f"{c} [{cur}]: ")
                if val:
                    setattr(obj, c, _parse_value(col, val))
        
        session.commit()


def _delete_item(stdscr, model: ModelType) -> None:
    item_id = _prompt(stdscr, "id to delete: ")
    confirm = _prompt(stdscr, "Are you sure? (y/N): ").lower()
    if confirm != "y":
        return
    with SessionLocal() as session:
        obj = session.get(model, int(item_id))
        if obj:
            session.delete(obj)
            session.commit()


def _manage_screen(stdscr, model: ModelType, filters=None) -> None:
    while True:
        with SessionLocal() as session:
            query = session.query(model)
            if filters is not None:
                query = query.filter(filters)
            items = query.order_by(model.id).all()
        _display_table(stdscr, model, items)
        h, _ = stdscr.getmaxyx()
        stdscr.addstr(h - 4, 0, "(a)dd (e)dit (d)elete (q)back")
        stdscr.refresh()
        key = stdscr.getch()
        if key == ord("a"):
            _add_item(stdscr, model)
        elif key == ord("e"):
            _edit_item(stdscr, model)
        elif key == ord("d"):
            _delete_item(stdscr, model)
        elif key == ord("q"):
            break


def _account_picker(stdscr) -> Optional[int]:
    """Show a menu to pick an account and return the account ID."""
    # First, check if we have any accounts to display
    with SessionLocal() as session:
        accounts = session.query(Account).filter(Account.archived == False).all()
        if not accounts:
            _prompt(stdscr, "No accounts found - press Enter")
            return None
        
        if len(accounts) == 1:
            # If only one account exists, return it automatically - no need for user selection
            return accounts[0].id
    
    # Prepare curses screen for menu navigation
    stdscr.clear()
    h, w = stdscr.getmaxyx()  # Get terminal dimensions
    curses.curs_set(0)  # Hide text cursor during menu navigation
    stdscr.keypad(True)  # Enable detection of arrow keys and special keys
    
    idx = 0  # Track which menu item is currently selected (0-based index)
    
    while True:
        # Redraw the entire menu each time (common curses pattern)
        stdscr.clear()
        stdscr.addstr(0, 0, "Select Account:")
        
        # Loop through accounts and display each one
        for i, account in enumerate(accounts):
            if i == idx:
                # Highlight the currently selected account with reverse video (white on black becomes black on white)
                stdscr.attron(curses.A_REVERSE)
            
            # Display account name and type with indentation for visual clarity
            display_text = f"{account.name} ({account.type})"
            stdscr.addstr(2 + i, 2, display_text)  # Row 2+i, column 2 for indentation
            
            if i == idx:
                # Turn off highlighting after this item (important for next items)
                stdscr.attroff(curses.A_REVERSE)
        
        # Display navigation instructions at bottom of screen
        stdscr.addstr(h - 2, 0, "↑↓ to navigate, Enter to select, q to cancel")
        stdscr.refresh()  # Make all changes visible on screen
        
        # Wait for user to press a key
        key = stdscr.getch()
        
        # Handle different key presses for navigation
        if key in (curses.KEY_UP, ord("k")):
            # Move selection up with wraparound (if at top, go to bottom)
            idx = (idx - 1) % len(accounts)
        elif key in (curses.KEY_DOWN, ord("j")):
            # Move selection down with wraparound (if at bottom, go to top)
            idx = (idx + 1) % len(accounts)
        elif key in (curses.KEY_ENTER, 10, 13):
            # User pressed Enter - confirm selection and return account ID
            return accounts[idx].id
        elif key in (ord("q"), ord("Q")):
            # User pressed q - cancel selection
            return None
    
    return None


def _display_ledger(stdscr, entries: List[LedgerEntry], account_name: str) -> None:
    """Display ledger entries in a scrollable table with curses formatting."""
    scroll_offset = 0  # Track current position in the ledger list for scrolling
    
    # Main display loop - continues until user presses 'q'
    while True:
        # Clear and prepare screen for table display
        stdscr.clear()
        h, w = stdscr.getmaxyx()  # Get current terminal size
        
        # Define column widths for consistent table formatting
        date_width = 12
        desc_width = 40
        amount_width = 12
        balance_width = 12
        type_width = 10
        
        # Create formatted table header with column alignment
        header = f"{'Date':<{date_width}} | {'Description':<{desc_width}} | {'Amount':>{amount_width}} | {'Balance':>{balance_width}} | {'Type':<{type_width}}"
        separator = "-" * len(header)  # Visual separator line under header
        
        # Display title and table header
        stdscr.addstr(0, 0, f"Ledger: {account_name}")
        stdscr.addstr(1, 0, header[:w])  # Truncate if wider than screen
        stdscr.addstr(2, 0, separator[:w])
        
        # Calculate how many data rows we can fit on screen
        max_rows = h - 5  # Reserve space for: title, header, separator, scroll info, instructions
        
        # Extract the portion of entries to display based on current scroll position
        display_entries = entries[scroll_offset:scroll_offset + max_rows]
        
        # Display each visible ledger entry as a formatted table row
        for i, entry in enumerate(display_entries):
            # Format each field to fit within its column width
            date_str = entry.date.strftime("%Y-%m-%d")
            desc_str = entry.description[:desc_width-1] if entry.description else ""
            amount_str = f"${entry.amount:,.2f}"  # Add $ and comma separators
            balance_str = f"${entry.balance:,.2f}"
            type_str = entry.transaction_type[:type_width-1]
            
            # Combine all columns into a single formatted row
            row = f"{date_str:<{date_width}} | {desc_str:<{desc_width}} | {amount_str:>{amount_width}} | {balance_str:>{balance_width}} | {type_str:<{type_width}}"
            
            # Apply visual styling based on transaction type
            if entry.transaction_type == 'recurring':
                # Dim recurring transactions to distinguish from one-time transactions
                stdscr.attron(curses.A_DIM)
            elif entry.amount < 0:
                # Bold negative amounts (expenses) to make them stand out
                stdscr.attron(curses.A_BOLD)
            
            # Display the row at the appropriate screen position
            stdscr.addstr(3 + i, 0, row[:w])  # Truncate row if wider than screen
            
            # Reset text attributes after each row to prevent bleeding into next row
            stdscr.attroff(curses.A_DIM)
            stdscr.attroff(curses.A_BOLD)
        
        # Display scroll position information for user orientation
        if entries:
            start_num = scroll_offset + 1
            end_num = min(scroll_offset + len(display_entries), len(entries))
            scroll_info = f"Showing {start_num}-{end_num} of {len(entries)} entries"
            stdscr.addstr(h - 3, 0, scroll_info)
        
        # Display navigation instructions at bottom
        instructions = "↑↓ to scroll, Page Up/Down for fast scroll, q to go back"
        stdscr.addstr(h - 2, 0, instructions[:w])
        stdscr.refresh()  # Make all changes visible
        
        # Wait for user input and handle scrolling commands
        key = stdscr.getch()
        
        if key in (curses.KEY_UP, ord("k")):
            # Scroll up by one row (with bounds checking)
            if scroll_offset > 0:
                scroll_offset -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            # Scroll down by one row (with bounds checking)
            if scroll_offset + max_rows < len(entries):
                scroll_offset += 1
        elif key == curses.KEY_PPAGE:  # Page Up key
            # Jump up by one screen's worth of entries
            scroll_offset = max(0, scroll_offset - max_rows)
        elif key == curses.KEY_NPAGE:  # Page Down key
            # Jump down by one screen's worth of entries
            max_scroll = max(0, len(entries) - max_rows)
            scroll_offset = min(max_scroll, scroll_offset + max_rows)
        elif key in (ord("q"), ord("Q")):
            # Exit ledger view and return to account selection
            break


def _ledger_screen(stdscr) -> None:
    """Main ledger screen coordinator - handles account selection and ledger display."""
    while True:
        # Step 1: Let user select which account's ledger to view
        account_id = _account_picker(stdscr)
        if account_id is None:
            return  # User cancelled account selection or no accounts exist
        
        # Step 2: Validate account and handle balance requirements
        with SessionLocal() as session:
            account = session.get(Account, account_id)
            if not account:
                _prompt(stdscr, "Account not found - press Enter")
                continue
            
            # Get account name while we're in the session context
            account_name = account.name
            
            # Check if we have a recent balance point to calculate from
            balance, balance_date = get_starting_balance(session, account_id)
            if balance is None:
                # No recent balance found - prompt user for current balance
                balance_str = _prompt(stdscr, f"Enter current balance for {account_name}: $")
                try:
                    # Parse user input, removing common formatting characters
                    balance = float(balance_str.replace("$", "").replace(",", ""))
                except ValueError:
                    _prompt(stdscr, "Invalid balance format - press Enter")
                    continue
                
                # Save the user-provided balance as a new balance point
                new_balance = Balance(
                    amount=balance,
                    timestamp=datetime.utcnow(),
                    account_id=account_id
                )
                session.add(new_balance)
                session.commit()
            
            # Step 3: Calculate ledger entries from balance point forward
            entries = get_account_ledger(session, account_id, initial_balance=balance)
            
            if not entries:
                _prompt(stdscr, "No transactions found for this account - press Enter")
                continue
        
        # Step 4: Display the ledger with scrolling capability
        _display_ledger(stdscr, entries, account_name)
        
        # Step 5: Ask if user wants to view another account's ledger
        another = _prompt(stdscr, "View another account? (y/N): ").lower()
        if another != 'y':
            break  # Exit ledger functionality entirely


def _get_menu_items():
    """Get menu items, including debug menu if in debug mode."""
    items = [
        ("Incomes", Recurring, Recurring.amount > 0),
        ("Bills", Recurring, Recurring.amount <= 0),
        ("Balance Points", Balance, None),
        ("Irregular Spending", IrregularCategory, None),
        ("Goals", Goal, None),
        ("Transactions", Transaction, None),
        ("Ledger", "ledger", None),
    ]
    
    # Add debug settings menu if in debug mode
    try:
        from . import debug
        if debug.is_debug_mode():
            items.append(("Debug Settings", "debug_settings", None))
    except ImportError:
        pass  # Debug module not available
    
    items.append(("Quit", None, None))
    return items


def _debug_settings_screen(stdscr) -> None:
    """Debug settings screen - only accessible in debug mode."""
    try:
        from . import debug
        if not debug.is_debug_mode():
            _prompt(stdscr, "Debug mode not active - press Enter")
            return
    except ImportError:
        _prompt(stdscr, "Debug module not available - press Enter")
        return
    
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        
        # Get current debug info
        debug_info = debug.get_debug_info()
        settings = debug_info['settings']
        
        # Display debug status
        stdscr.addstr(0, 0, "🔧 Debug Settings")
        stdscr.addstr(2, 0, f"Debug Mode: {'✅ Active' if debug_info['debug_mode'] else '❌ Inactive'}")
        stdscr.addstr(3, 0, f"Database: {debug_info['db_file']}")
        stdscr.addstr(4, 0, f"Backup: {'✅ Available' if debug_info['backup_exists'] else '❌ Not found'}")
        
        # Display stats if available
        if 'stats' in debug_info:
            stats = debug_info['stats']
            if 'error' in stats:
                stdscr.addstr(6, 0, f"Stats: {stats['error']}")
            else:
                stdscr.addstr(6, 0, "Database Stats:")
                stdscr.addstr(7, 2, f"Accounts: {stats.get('accounts', 0)}")
                stdscr.addstr(8, 2, f"Transactions: {stats.get('transactions', 0)}")
                stdscr.addstr(9, 2, f"Recurring: {stats.get('recurring', 0)}")
                stdscr.addstr(10, 2, f"Balance Points: {stats.get('balance_points', 0)}")
        
        # Display current settings
        stdscr.addstr(12, 0, "Current Settings:")
        stdscr.addstr(13, 2, f"Ledger Days: {settings.get('ledger_days', 60)}")
        stdscr.addstr(14, 2, f"Auto Balance Creation: {'✅' if settings.get('auto_balance_creation') else '❌'}")
        stdscr.addstr(15, 2, f"Show Debug Info: {'✅' if settings.get('show_debug_info') else '❌'}")
        
        # Instructions
        stdscr.addstr(h - 4, 0, "Actions:")
        stdscr.addstr(h - 3, 0, "d) Change ledger days  r) Reload test data")
        stdscr.addstr(h - 2, 0, "c) Create balance point  q) Back to menu")
        stdscr.refresh()
        
        # Handle user input
        key = stdscr.getch()
        
        if key in (ord("d"), ord("D")):
            # Change ledger days
            _change_ledger_days(stdscr, debug)
            
        elif key in (ord("r"), ord("R")):
            # Reload test data
            _prompt(stdscr, "Reloading test data...")
            from .test_data import create_test_data
            create_test_data()
            _prompt(stdscr, "Test data reloaded - press Enter")
            
        elif key in (ord("c"), ord("C")):
            # Create balance point
            account_input = _prompt(stdscr, "Account ID (or Enter for 1): ")
            account_id = 1
            try:
                if account_input.strip():
                    account_id = int(account_input)
            except ValueError:
                _prompt(stdscr, "Invalid account ID - press Enter")
                continue
                
            balance_input = _prompt(stdscr, "Balance amount: $")
            try:
                balance = float(balance_input.replace("$", "").replace(",", ""))
                if debug.create_quick_balance(account_id, balance):
                    _prompt(stdscr, f"Balance ${balance:,.2f} created for account {account_id} - press Enter")
                else:
                    _prompt(stdscr, "Failed to create balance - press Enter")
            except ValueError:
                _prompt(stdscr, "Invalid balance amount - press Enter")
                
        elif key in (ord("q"), ord("Q")):
            break


def _change_ledger_days(stdscr, debug_module) -> None:
    """Allow user to change ledger calculation days."""
    # Get date range options from debug module
    date_options = debug_module.get_date_range_options()
    current_days = debug_module.get_ledger_days()
    
    # Show selection menu
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    curses.curs_set(0)
    stdscr.keypad(True)
    
    idx = 0
    # Find current selection if it matches a preset
    for i, (_, days) in enumerate(date_options):
        if days == current_days:
            idx = i
            break
    
    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, f"Select Ledger Calculation Period (Current: {current_days} days)")
        stdscr.addstr(1, 0, "-" * 50)
        
        # Display options with current selection highlighted
        for i, (label, days) in enumerate(date_options):
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
            
            if days == -1:
                stdscr.addstr(3 + i, 2, f"{label}")
            else:
                stdscr.addstr(3 + i, 2, f"{label} ({days} days)")
            
            if i == idx:
                stdscr.attroff(curses.A_REVERSE)
        
        stdscr.addstr(h - 2, 0, "↑↓ to navigate, Enter to select, q to cancel")
        stdscr.refresh()
        
        key = stdscr.getch()
        
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(date_options)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(date_options)
        elif key in (curses.KEY_ENTER, 10, 13):
            label, days = date_options[idx]
            
            if days == -1:  # Custom date option
                # Handle custom date input
                date_input = _prompt(stdscr, "Enter target date (YYYY-MM-DD, MM/DD/YYYY, etc.): ")
                custom_days = debug_module.parse_custom_date(date_input)
                
                if custom_days is not None:
                    debug_module.set_ledger_days(custom_days)
                    from datetime import date, timedelta
                    target_date = date.today() + timedelta(days=custom_days)
                    _prompt(stdscr, f"Ledger will calculate to {target_date} ({custom_days} days) - press Enter")
                else:
                    _prompt(stdscr, "Invalid date format or date is in the past - press Enter")
            else:
                # Standard preset option
                debug_module.set_ledger_days(days)
                _prompt(stdscr, f"Ledger calculation set to {days} days - press Enter")
            
            break
            
        elif key in (ord("q"), ord("Q")):
            break


def _run_menu(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    idx = 0
    
    while True:
        # Get current menu items (may include debug menu)
        menu_items = _get_menu_items()
        
        stdscr.clear()
        
        # Show title with debug indicator if in debug mode
        title = "Budget Lists"
        try:
            from . import debug
            if debug.is_debug_mode():
                title += " 🔧 [DEBUG MODE]"
        except ImportError:
            pass
            
        stdscr.addstr(0, 0, title)
        
        # Display menu items
        for i, (label, _, _) in enumerate(menu_items):
            if i == idx:
                stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(2 + i, 2, label)
            if i == idx:
                stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()
        
        # Handle input
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(menu_items)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(menu_items)
        elif key in (curses.KEY_ENTER, 10, 13):
            label, model, filt = menu_items[idx]
            if model is None:
                break
            elif model == "ledger":
                # Special case for ledger screen
                _ledger_screen(stdscr)
            elif model == "debug_settings":
                # Special case for debug settings screen
                _debug_settings_screen(stdscr)
            else:
                _manage_screen(stdscr, model, filt)
        elif key in (ord("q"), ord("Q")):
            break


def main() -> None:
    init_db()
    curses.wrapper(_run_menu)

