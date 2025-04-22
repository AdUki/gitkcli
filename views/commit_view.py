"""
Commit list view
"""
import curses
from views.base_view import BaseView

class CommitView(BaseView):
    """Commit list view"""
    
    def __init__(self, stdscr, repository):
        """
        Initialize commit view
        
        Args:
            stdscr: Curses window object
            repository: Repository instance
        """
        super().__init__(stdscr)
        self.repository = repository
        self.commits = repository.commits
        self.current_index = 0
        self.top_index = 0
        self.graph_width = 15  # Width allocated for the commit graph (future use)
        
    def draw(self):
        """Draw the commit list view"""
        if not self.commits:
            self.stdscr.addstr(0, 0, "No commits to display", curses.color_pair(1))
            return
            
        # Draw header
        self.draw_header("GITK CLI - Commit History")
        
        # Draw commit list
        self._draw_commits()
        
        # Draw status line
        status = f" Commit {self.current_index + 1}/{len(self.commits)} | Press 'h' for help "
        self.draw_status(status)
        
    def _draw_commits(self):
        """Draw the list of commits"""
        # Calculate visible lines
        display_count = min(len(self.commits), self.max_lines - 2)
        
        for i in range(display_count):
            idx = self.top_index + i
            if idx >= len(self.commits):
                break
                
            commit = self.commits[idx]
            is_selected = idx == self.current_index
            
            # Format the line
            line = f"{commit.short_id} {commit.date} {commit.author}: {commit.message}"
            
            # Add ref indicators
            if commit.refs:
                line += commit.formatted_refs
                
            # Truncate if too long
            if len(line) > self.max_cols:
                line = line[:self.max_cols - 3] + "..."
                
            # Draw the line
            attr = curses.color_pair(9) if is_selected else curses.color_pair(1)
            try:
                self.stdscr.addstr(i + 1, 0, line.ljust(self.max_cols - 1), attr)
            except curses.error:
                # Ignore potential curses errors
                pass
    
    def _handle_specific_key(self, key):
        """
        Handle commit view specific keys
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        if not self.commits:
            return True, False, None
            
        # Handle navigation keys
        if self.handle_navigation_keys(key, self.move_selection):
            return True, False, None
            
        # Handle other commit-specific keys
        if key == ord('g'):
            self.current_index = 0
            self.top_index = 0
        elif key == ord('G'):
            self.current_index = len(self.commits) - 1
            self.top_index = max(0, self.current_index - (self.max_lines - 3))
        elif key == 10:  # Enter key
            return True, True, f"diff:{self.commits[self.current_index].id}"
        elif key == ord('c'):
            # Copy commit ID to clipboard
            commit_id = self.commits[self.current_index].id
            return True, True, f"copy:{commit_id}"
        elif key == ord('r'):
            # Refresh commits - sends refresh command to controller
            return True, True, "refresh"
        
        return True, False, None  # Continue program, no view change
        
    def move_selection(self, delta):
        """
        Move the selection by delta
        
        Args:
            delta: Number of positions to move
        """
        if not self.commits:
            return
            
        old_index = self.current_index
        self.current_index = max(0, min(len(self.commits) - 1, self.current_index + delta))
        
        # Adjust top_index if needed
        visible_lines = self.max_lines - 3
        if self.current_index < self.top_index:
            self.top_index = self.current_index
        elif self.current_index >= self.top_index + visible_lines:
            self.top_index = self.current_index - visible_lines + 1
