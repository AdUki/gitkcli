"""
Diff view for displaying commit changes
"""
import curses
from views.base_view import BaseView

class DiffView(BaseView):
    """View for displaying commit diff"""
    
    def __init__(self, stdscr, repository, commit_id):
        """
        Initialize diff view
        
        Args:
            stdscr: Curses window object
            repository: Repository instance
            commit_id: ID of commit to display
        """
        super().__init__(stdscr)
        self.repository = repository
        self.commit_id = commit_id
        self.commit = repository.commit_map.get(commit_id)
        self.diff_top = 0
        self.diff_cursor = 0
        
        # Load diff if not already loaded
        if self.commit and not hasattr(self.commit, 'diff') or self.commit.diff is None:
            self.commit.diff = repository.get_commit_diff(commit_id)
            
    def draw(self):
        """Draw the diff view"""
        if not self.commit:
            self.stdscr.addstr(0, 0, "Commit not found", curses.color_pair(1))
            return
            
        # Draw header
        header = f"Diff for {self.commit.short_id}: {self.commit.message}"
        self.draw_header(header)
        
        # Draw diff content
        self._draw_diff_content()
        
        # Draw status line
        diff_length = len(self.commit.diff) if self.commit.diff else 0
        status = f" Lines {self.diff_top + 1}-{min(self.diff_top + self.max_lines - 2, diff_length)}/{diff_length} | Press ENTER to return "
        self.draw_status(status)
        
    def _draw_diff_content(self):
        """Draw the diff content"""
        if not self.commit.diff:
            self.stdscr.addstr(1, 0, "No diff available", curses.color_pair(1))
            return
            
        # Calculate visible lines
        display_count = min(len(self.commit.diff), self.max_lines - 2)
        
        for i in range(display_count):
            idx = self.diff_top + i
            if idx >= len(self.commit.diff):
                break
                
            diff_type, line = self.commit.diff[idx]
            
            # Set color based on line type
            if diff_type == 'file':
                attr = curses.color_pair(8)
            elif diff_type == 'add':
                attr = curses.color_pair(7)
            elif diff_type == 'del':
                attr = curses.color_pair(6)
            elif diff_type == 'hunk':
                attr = curses.color_pair(4)
            else:
                attr = curses.color_pair(1)
                
            # Highlight the line with cursor
            if idx == self.diff_top + self.diff_cursor:
                attr |= curses.A_REVERSE
                
            # Truncate if too long
            if len(line) > self.max_cols:
                line = line[:self.max_cols - 3] + "..."
                
            # Draw the line
            try:
                self.stdscr.addstr(i + 1, 0, line.ljust(self.max_cols - 1), attr)
            except curses.error:
                # Ignore potential curses errors
                pass
                
    def _handle_specific_key(self, key):
        """
        Handle diff view specific keys
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        if not self.commit or not self.commit.diff:
            if key == 10:  # Enter key
                return True, True, "commit"  # Back to commit view
            return True, False, None
            
        # Custom navigation for diff view with cursor
        max_cursor = min(self.max_lines - 3, len(self.commit.diff) - self.diff_top - 1)
        
        if key == 10:  # Enter key
            return True, True, "commit"  # Back to commit view
        elif key == ord('j') or key == curses.KEY_DOWN:
            if self.diff_cursor < max_cursor and self.diff_top + self.diff_cursor < len(self.commit.diff) - 1:
                self.diff_cursor += 1
            else:
                self._scroll_diff(1)
        elif key == ord('k') or key == curses.KEY_UP:
            if self.diff_cursor > 0:
                self.diff_cursor -= 1
            else:
                self._scroll_diff(-1)
        elif key == ord('d') or key == curses.KEY_NPAGE:  # Page Down
            page_size = self.max_lines - 3
            self._scroll_diff(page_size)
            self.diff_cursor = 0  # Reset cursor position when page down
        elif key == ord('u') or key == curses.KEY_PPAGE:  # Page Up
            page_size = self.max_lines - 3
            self._scroll_diff(-page_size)
            self.diff_cursor = 0  # Reset cursor position when page up
        elif key == ord('b'):
            # Get file path at cursor
            file_path = self.get_current_file_path()
            if file_path:
                return True, True, f"blame:{self.commit_id}:{file_path}"
        
        return True, False, None  # Continue program, no view change
        
    def _scroll_diff(self, delta):
        """
        Scroll the diff by delta lines
        
        Args:
            delta: Number of lines to scroll
        """
        if not self.commit.diff:
            return
            
        old_top = self.diff_top
        self.diff_top = max(0, min(len(self.commit.diff) - 1, self.diff_top + delta))
        
    def get_current_file_path(self):
        """
        Extract the file path at current cursor position
        
        Returns:
            str: File path or None
        """
        if not self.commit.diff:
            return None
            
        current_line_idx = self.diff_top + self.diff_cursor
        if current_line_idx < 0 or current_line_idx >= len(self.commit.diff):
            return None
            
        # Search backward from current position to find the last file header
        for i in range(current_line_idx, -1, -1):
            diff_type, line = self.commit.diff[i]
            if diff_type == 'file' and line.startswith('diff --git'):
                # Extract the file path from the diff header
                # Format is typically: diff --git a/path/to/file b/path/to/file
                parts = line.split()
                if len(parts) >= 4:
                    # Use the 'b' path (new file path)
                    return parts[3][2:]  # Remove the 'b/' prefix
                    
        return None
