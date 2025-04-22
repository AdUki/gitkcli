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
            # Show origin of current line
            self._show_line_origin()
        
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

    def _show_line_origin(self):
        """Show the origin of the current line at cursor position"""
        if not self.commit or not self.commit.diff:
            return
            
        current_line_idx = self.diff_top + self.diff_cursor
        if current_line_idx < 0 or current_line_idx >= len(self.commit.diff):
            return
            
        diff_type, line_content = self.commit.diff[current_line_idx]
        
        # Only show origin for content lines (added, deleted or context)
        if diff_type not in ('add', 'del', 'context'):
            self._show_message("Not a content line")
            return
            
        # Find the file path for the current position
        file_path = self.get_current_file_path()
        if not file_path:
            self._show_message("Could not determine file path")
            return
            
        # Get origin information
        origin_info = self.repository.get_line_origin(self.commit_id, file_path, line_content, diff_type)
        if not origin_info:
            self._show_message("Origin not found for this line")
            return
            
        # Display origin information in a popup
        self._show_origin_popup(origin_info, line_content)
    
    def _show_origin_popup(self, origin_info, line_content):
        """
        Show a popup with origin information
        
        Args:
            origin_info: Tuple of (commit_id, author, date, message)
            line_content: Content of the line
        """
        commit_id, author, date, message = origin_info
        
        # Create popup content
        popup_content = [
            f"Origin of: {line_content[:60] + '...' if len(line_content) > 60 else line_content}",
            "",
            f"Commit: {commit_id}",
            f"Author: {author}",
            f"Date:   {date}",
            f"",
            f"Message: {message[:60] + '...' if len(message) > 60 else message}",
            "",
            "Press any key to close"
        ]
        
        # Calculate popup dimensions
        popup_height = len(popup_content) + 2  # Add 2 for border
        popup_width = max(min(self.max_cols - 10, 80), 60)  # Width between 60 and 80, or smaller if screen is small
        
        # Calculate popup position
        popup_y = max(0, (self.max_lines // 2) - (popup_height // 2))
        popup_x = max(0, (self.max_cols // 2) - (popup_width // 2))
        
        # Create popup window
        popup = curses.newwin(popup_height, popup_width, popup_y, popup_x)
        popup.box()
        
        # Draw content
        for i, line in enumerate(popup_content):
            # Ensure line fits in popup width
            if len(line) > popup_width - 4:
                line = line[:popup_width - 7] + "..."
                
            # Draw line
            try:
                attr = curses.A_BOLD if i == 0 or i == 2 else curses.A_NORMAL
                popup.addstr(i + 1, 2, line, attr)
            except curses.error:
                pass
        
        # Refresh popup and get input
        popup.refresh()
        popup.getch()  # Wait for any key
    
    def _show_message(self, message):
        """Show a temporary message"""
        max_y, max_x = self.stdscr.getmaxyx()
        y_pos = max_y // 2
        x_pos = max(0, (max_x - len(message)) // 2)
        
        # Save area under the message
        backup = []
        try:
            for dy in range(3):
                line = []
                for dx in range(len(message) + 4):
                    if y_pos - 1 + dy < max_y and x_pos - 2 + dx < max_x:
                        ch = self.stdscr.inch(y_pos - 1 + dy, x_pos - 2 + dx)
                        line.append(ch)
                backup.append(line)
        except curses.error:
            pass
            
        # Create a small popup window
        try:
            popup = curses.newwin(3, len(message) + 4, y_pos - 1, x_pos - 2)
            popup.box()
            popup.addstr(1, 2, message)
            popup.refresh()
            curses.napms(1500)  # Show for 1.5 seconds
        except curses.error:
            pass
            
        # Restore the screen
        try:
            for dy in range(3):
                for dx in range(len(message) + 4):
                    if y_pos - 1 + dy < max_y and x_pos - 2 + dx < max_x and dy < len(backup) and dx < len(backup[dy]):
                        self.stdscr.addch(y_pos - 1 + dy, x_pos - 2 + dx, backup[dy][dx])
        except curses.error:
            pass
            
        self.stdscr.refresh()
