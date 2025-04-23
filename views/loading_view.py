"""
Loading view to display while loading commits
"""
import curses
from views.base_view import BaseView

class LoadingView(BaseView):
    """View displayed while loading commits"""
    
    def __init__(self, stdscr, message="Loading commits..."):
        """
        Initialize loading view
        
        Args:
            stdscr: Curses window object
            message: Loading message to display
        """
        super().__init__(stdscr)
        self.message = message
        
    def draw(self):
        """Draw the loading view"""
        # Center the loading message
        x_pos = self.max_cols // 2 - len(self.message) // 2
        y_pos = self.max_lines // 2
        
        try:
            self.stdscr.addstr(y_pos, x_pos, self.message, curses.color_pair(1))
        except curses.error:
            # Ignore potential curses errors
            pass
            
    def handle_key(self, key):
        """
        Handle key press for loading view
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Exit application
        if key == ord('q'):
            return False, False, None
        
        # Ignore all other keys while loading
        return True, False, None
