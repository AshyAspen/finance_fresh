"""Entry point for running the curses-based budget UI."""

import sys
import atexit

from .tui import main


def entry_point() -> None:
    # Check for debug flag
    debug_flag = "--debug" in sys.argv
    
    if debug_flag:
        # Import debug module only when needed
        from . import debug
        
        # Enable debug mode and set up test environment
        debug.enable_debug_mode()
        
        # Register cleanup function to restore on exit
        atexit.register(debug.disable_debug_mode)
        
        # Remove debug flag from sys.argv so it doesn't interfere
        sys.argv = [arg for arg in sys.argv if arg != "--debug"]
    else:
        # Check if we're exiting debug mode and need to restore
        from . import debug
        
        # If there's a backup and we're not in debug mode, restore
        if not debug.is_debug_mode():
            debug.disable_debug_mode()
    
    # Start the main application
    main()


if __name__ == "__main__":  # pragma: no cover
    entry_point()
