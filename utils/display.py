"""
Display utilities
"""
import curses
import time

def setup_colors():
    """
    Set up color pairs for curses
    """
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, -1)     # Default
    curses.init_pair(2, curses.COLOR_YELLOW, -1)    # Commit ID
    curses.init_pair(3, curses.COLOR_GREEN, -1)     # Author
    curses.init_pair(4, curses.COLOR_CYAN, -1)      # Date
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)   # Search match
    curses.init_pair(6, curses.COLOR_RED, -1)       # Diff deletion
    curses.init_pair(7, curses.COLOR_GREEN, -1)     # Diff addition
    curses.init_pair(8, curses.COLOR_BLUE, -1)      # Diff file
    curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Header/status
    curses.init_pair(11, curses.COLOR_YELLOW, -1)    # Tag
    curses.init_pair(12, curses.COLOR_GREEN, -1)     # Branch

    # Selected versions of colors (+100)
    curses.init_pair(100 + 1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(100 + 5, curses.COLOR_MAGENTA, curses.COLOR_BLUE)
    curses.init_pair(100 + 11, curses.COLOR_YELLOW, curses.COLOR_BLUE)
    curses.init_pair(100 + 12, curses.COLOR_GREEN, curses.COLOR_BLUE)


def show_message(stdscr, message, delay=2):
    """
    Show a temporary message on screen
    
    Args:
        stdscr: Curses window object
        message (str): Message to display
        delay (int): Seconds to display message
    """
    max_y, max_x = stdscr.getmaxyx()
    
    # Clear screen
    stdscr.clear()
    
    # Display message
    try:
        stdscr.addstr(max_y // 2, max_x // 2 - len(message) // 2, message)
    except curses.error:
        # Fallback if centered positioning fails
        stdscr.addstr(0, 0, message)
        
    stdscr.refresh()
    time.sleep(delay)
