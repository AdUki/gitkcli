"""
Base abstract view class
"""
import curses
from abc import ABC, abstractmethod

class BaseView(ABC):
    """Abstract base class for all views"""
    
    def __init__(self, stdscr):
        """
        Initialize a view
        
        Args:
            stdscr: Curses window object
        """
        self.stdscr = stdscr
        self.max_lines, self.max_cols = stdscr.getmaxyx()
        
    def refresh(self):
        """Refresh the screen dimensions and display"""
        self.max_lines, self.max_cols = self.stdscr.getmaxyx()
        self.stdscr.clear()
        self.draw()
        self.stdscr.refresh()
        
    @abstractmethod
    def draw(self):
        """Draw the view - must be implemented by subclasses"""
        pass
        
    def handle_key(self, key):
        """
        Handle key press
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Default implementation - subclasses should override this
        if key == ord('q'):
            return False, False, None  # Exit program
        elif key == ord('H'):
            return True, True, "help"  # Switch to help view
        elif key == curses.KEY_RESIZE:
            return True, False, None  # Handle resize event
            
        return True, False, None  # Continue program, no view change
    
    def get_safe_dimensions(self):
        """Get safe dimensions for writing to screen"""
        # Subtract 1 from max_cols to avoid writing to the bottom-right corner
        return self.max_lines, self.max_cols - 1
        
    def draw_header(self, text, attr=None):
        """
        Draw header text
        
        Args:
            text: Header text
            attr: Curses attribute for styling
        """
        if attr is None:
            attr = curses.color_pair(10)
            
        # Truncate if too long
        if len(text) > self.max_cols:
            text = text[:self.max_cols - 3] + "..."
            
        try:
            self.stdscr.addstr(0, 0, text.ljust(self.max_cols - 1), attr)
        except curses.error:
            # Ignore potential curses errors
            pass
    
    def draw_status(self, text, attr=None):
        """
        Draw status line at bottom of screen
        
        Args:
            text: Status text
            attr: Curses attribute for styling
        """
        if attr is None:
            attr = curses.color_pair(10)
            
        try:
            # Avoid writing to the bottom-right corner
            self.stdscr.addstr(self.max_lines - 1, 0, text.ljust(self.max_cols - 1), attr)
        except curses.error:
            # Ignore error from writing to bottom right corner
            pass
