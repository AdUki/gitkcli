"""
Help view for displaying keyboard shortcuts and commands
"""
import curses
from views.base_view import BaseView

class HelpView(BaseView):
    """View for displaying help information"""
    
    def __init__(self, stdscr):
        """
        Initialize help view
        
        Args:
            stdscr: Curses window object
        """
        super().__init__(stdscr)
        self.help_text = self._generate_help_text()
        
    def _generate_help_text(self):
        """Generate help text content"""
        return [
            "GITK CLI HELP",
            "",
            "Navigation:",
            "  j, DOWN    : Move down",
            "  k, UP      : Move up",
            "  g          : Go to top",
            "  G          : Go to bottom",
            "  d, PgDn    : Page down",
            "  u, PgUp    : Page up",
            "",
            "Actions:",
            "  ENTER      : Show/hide diff for selected commit",
            "  c          : Copy commit ID to clipboard",
            "  f          : Find (search) in commit messages",
            "  r          : Refresh commit list",
            "",
            "Diff View:",
            "  j, DOWN    : Move down",
            "  k, UP      : Move up", 
            "  d, PgDn    : Page down",
            "  u, PgUp    : Page up",
            "  ENTER      : Return to commit list",
            "  b          : Show origin of the line at cursor",
            "",
            "Line Origin:",
            "  j          : Jump to the origin commit",
            "  Any key    : Close the popup",
            "",
            "Views:",
            "  h          : Show/hide this help",
            "  q          : Quit",
            "",
            "Press any key to close help"
        ]
        
    def draw(self):
        """Draw the help view"""
        # Draw each line of help text
        for i, line in enumerate(self.help_text):
            if i >= self.max_lines:
                break
                
            # First line is header
            attr = curses.color_pair(10) if i == 0 else curses.color_pair(1)
            
            try:
                self.stdscr.addstr(i, 0, line.ljust(self.max_cols - 1), attr)
            except curses.error:
                # Ignore potential curses errors
                pass
                
    def _handle_specific_key(self, key):
        """
        Handle help view specific keys - any key returns to previous view
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Any key closes help and returns to previous view
        return True, True, "previous"
