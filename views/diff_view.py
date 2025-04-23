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
        self.search_string = ""
        self.search_active = False
        self.search_results = []
        self.search_index = -1
        self.h_scroll = 0  # Horizontal scroll position
        
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
        
        # Draw refs line if commit has refs
        if self.commit.refs:
            self._draw_refs_line()
        
        # Draw diff content with proper offset based on whether refs line is shown
        self._draw_diff_content()
        
        # Draw status line
        if self.search_active:
            status = f" Search: {self.search_string}"
            self.draw_status(status)
        else:
            diff_length = len(self.commit.diff) if self.commit.diff else 0
            status = f" Lines {self.diff_top + 1}-{min(self.diff_top + self.max_lines - 2, diff_length)}/{diff_length} | Scroll: {self.h_scroll} | Press ENTER to return "
            self.draw_status(status)
    
    def _draw_refs_line(self):
        """Draw a line showing the refs for this commit"""
        if not self.commit.refs:
            return
        
        # Format refs text
        refs_text = "Refs: "
        tags = []
        branches = []
        other_refs = []
        
        for ref in self.commit.refs:
            if ref.startswith("tag: "):
                ref_name = ref[5:]  # Remove "tag: " prefix
                tags.append(f"<{ref_name}>")
            else:
                branches.append(f"[{ref}]")
        
        # Combine all refs
        all_refs = []
        if tags:
            all_refs.extend(tags)
        if branches:
            all_refs.extend(branches)
        if other_refs:
            all_refs.extend(other_refs)
            
        refs_text += ", ".join(all_refs)
        
        # Apply horizontal scrolling to the visible portion
        visible_text = refs_text[self.h_scroll:self.h_scroll + self.max_cols - 1]
        
        # Draw the refs line
        try:
            self.stdscr.addstr(1, 0, visible_text, curses.color_pair(1))
            
            # Fill the rest of the line with spaces
            if len(visible_text) < self.max_cols - 1:
                self.stdscr.addstr(1, len(visible_text), " " * (self.max_cols - 1 - len(visible_text)), curses.color_pair(1))
                
            # Colorize the tags and branches in the visible text
            pos = 0
            start_pos = 0
            
            # Find "Refs: " in the visible text
            if "Refs: " in visible_text:
                start_pos = visible_text.find("Refs: ") + 6
                pos = start_pos
            else:
                # If "Refs: " is scrolled off, start from beginning
                pos = 0
            
            # Parse the visible text to find and colorize refs
            while pos < len(visible_text):
                # Check for tags (inside angle brackets)
                tag_start = visible_text.find('<', pos)
                if tag_start != -1 and tag_start < len(visible_text):
                    tag_end = visible_text.find('>', tag_start)
                    if tag_end != -1 and tag_end < len(visible_text):
                        # Color the tag
                        tag_text = visible_text[tag_start:tag_end+1]
                        self.stdscr.addstr(1, tag_start, tag_text, curses.color_pair(11))
                        pos = tag_end + 1
                        continue
                
                # Check for branches (inside square brackets)
                branch_start = visible_text.find('[', pos)
                if branch_start != -1 and branch_start < len(visible_text):
                    branch_end = visible_text.find(']', branch_start)
                    if branch_end != -1 and branch_end < len(visible_text):
                        # Color the branch
                        branch_text = visible_text[branch_start:branch_end+1]
                        self.stdscr.addstr(1, branch_start, branch_text, curses.color_pair(12))
                        pos = branch_end + 1
                        continue
                
                # Move to next position if no tag or branch found
                pos += 1
            
        except curses.error:
            # Ignore potential curses errors
            pass
        
    def _draw_diff_content(self):
        """Draw the diff content"""
        if not self.commit.diff:
            self.stdscr.addstr(2, 0, "No diff available", curses.color_pair(1))
            return
        
        # Start at line 2 if we're showing refs, otherwise line 1
        start_line = 2 if self.commit.refs else 1
        
        # Calculate visible lines
        display_count = min(len(self.commit.diff), self.max_lines - start_line - 1)
        
        for i in range(display_count):
            idx = self.diff_top + i
            if idx >= len(self.commit.diff):
                break
                
            line_num = i + start_line  # Adjust line number based on refs display
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
                
            # Highlight search matches
            if self.search_string and idx in self.search_results:
                # Preserve original color but add bold
                attr |= curses.A_BOLD
                # If this is also cursor position, maintain reverse video
                if idx == self.diff_top + self.diff_cursor:
                    attr |= curses.A_REVERSE
            
            try:
                # Apply horizontal scrolling
                visible_line = line[self.h_scroll:self.h_scroll + self.max_cols - 1]
                self.stdscr.addstr(line_num, 0, visible_line, attr)
                
                # Fill the rest of the line with spaces
                if len(visible_line) < self.max_cols - 1:
                    self.stdscr.addstr(line_num, len(visible_line), " " * (self.max_cols - 1 - len(visible_line)), attr)
                    
            except curses.error:
                # Ignore potential curses errors
                pass
                
    def handle_key(self, key):
        """
        Override the base handle_key to properly handle search mode
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Direct handling for search mode
        if self.search_active:
            return self._handle_search_input(key)
            
        # Handle horizontal scrolling directly
        if key == ord('h') or key == curses.KEY_LEFT:
            self.h_scroll = max(0, self.h_scroll - 5)
            return True, False, None
        elif key == ord('l') or key == curses.KEY_RIGHT:
            self.h_scroll += 5
            return True, False, None
        
        # Use the default handler for other keys
        return super().handle_key(key)
                
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
            
        # Check for search key
        if key == ord('/'):
            self.search_active = True
            self.search_string = ""
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
        elif key == ord('g'):
            # Go to top
            self.diff_top = 0
            self.diff_cursor = 0
        elif key == ord('G'):
            # Go to bottom
            if len(self.commit.diff) > self.max_lines - 2:
                self.diff_top = len(self.commit.diff) - (self.max_lines - 2)
                self.diff_cursor = self.max_lines - 3
            else:
                self.diff_top = 0
                self.diff_cursor = len(self.commit.diff) - 1
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
            result = self._show_line_origin()
            if result:
                return result  # Return the result if jumping to a commit
        elif key == ord('n'):
            # Jump to next search result
            self._next_search_result()
        elif key == ord('N'):
            # Jump to previous search result
            self._prev_search_result()
        
        return True, False, None  # Continue program, no view change
        
    def _handle_search_input(self, key):
        """
        Handle key input while in search mode
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        if key == ord('q'):  # Allow quitting from search mode
            return False, False, None
        elif key == 27:  # Escape key
            # Cancel search
            self.search_active = False
        elif key == 10 or key == curses.KEY_ENTER:  # Enter key
            # Complete search
            self.search_active = False
            self._perform_search()
            if self.search_results:
                self._next_search_result()
        elif key == 127 or key == curses.KEY_BACKSPACE:  # Backspace
            if self.search_string:
                self.search_string = self.search_string[:-1]
        elif key == curses.KEY_DC:  # Delete key
            if self.search_string:
                self.search_string = self.search_string[:-1]
        elif 32 <= key <= 126:  # Printable ASCII
            self.search_string += chr(key)
            
        return True, False, None
            
    def _perform_search(self):
        """Perform search with current search string"""
        self.search_results = []
        self.search_index = -1
        
        search_term = self.search_string.lower()
        if not search_term:
            return
            
        # Search in diff lines
        for i, (diff_type, line) in enumerate(self.commit.diff):
            if search_term in line.lower():
                self.search_results.append(i)
                
    def _next_search_result(self):
        """Move to next search result"""
        if not self.search_results:
            return
            
        current_pos = self.diff_top + self.diff_cursor
            
        # Find next result after current position
        next_idx = None
        for idx in self.search_results:
            if idx > current_pos:
                next_idx = idx
                break
                
        # Wrap around if no next result
        if next_idx is None and self.search_results:
            next_idx = self.search_results[0]
            
        if next_idx is not None:
            # Move cursor to the search result
            self.diff_cursor = min(self.max_lines - 3, next_idx - self.diff_top)
            
            # If result is outside visible area, scroll to it
            if next_idx < self.diff_top or next_idx >= self.diff_top + self.max_lines - 2:
                self.diff_top = max(0, next_idx - (self.max_lines // 4))
                self.diff_cursor = next_idx - self.diff_top
                
    def _prev_search_result(self):
        """Move to previous search result"""
        if not self.search_results:
            return
            
        current_pos = self.diff_top + self.diff_cursor
            
        # Find previous result before current position
        prev_idx = None
        for idx in reversed(self.search_results):
            if idx < current_pos:
                prev_idx = idx
                break
                
        # Wrap around if no previous result
        if prev_idx is None and self.search_results:
            prev_idx = self.search_results[-1]
            
        if prev_idx is not None:
            # Move cursor to the search result
            self.diff_cursor = min(self.max_lines - 3, prev_idx - self.diff_top)
            
            # If result is outside visible area, scroll to it
            if prev_idx < self.diff_top or prev_idx >= self.diff_top + self.max_lines - 2:
                self.diff_top = max(0, prev_idx - (self.max_lines // 4))
                self.diff_cursor = prev_idx - self.diff_top
        
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
        result = self._show_origin_popup(origin_info, line_content)
        return result
    
    def _show_origin_popup(self, origin_info, line_content):
        """
        Show a popup with origin information
        
        Args:
            origin_info: Tuple of (commit_id, author, date, message)
            line_content: Content of the line
        """
        commit_id, author, date, message = origin_info
        
        # Get the complete commit message
        try:
            commit_obj = self.repository.repo.get(commit_id)
            full_message = commit_obj.message if commit_obj else message
            message_lines = full_message.strip().split('\n')
        except Exception:
            message_lines = [message]
        
        # Create popup content
        popup_content = [
            f"Origin of: {line_content[:60] + '...' if len(line_content) > 60 else line_content}",
            "",
            f"Commit: {commit_id}",
            f"Author: {author}",
            f"Date:   {date}",
            "",
            "Message:",
        ]
        
        # Add the full commit message, respecting line breaks
        for line in message_lines:
            popup_content.append(f"  {line}")
            
        popup_content.append("")
        popup_content.append("Press 'j' to jump to this commit, any other key to close")
        
        # Calculate popup dimensions
        popup_width = max(min(self.max_cols - 10, 80), 60)  # Width between 60 and 80, or smaller if screen is small
        
        # Format content to fit in popup width
        formatted_content = []
        for line in popup_content:
            if len(line) > popup_width - 4:
                line = line[:popup_width - 7] + "..."
            formatted_content.append(line)
            
        popup_height = min(len(formatted_content) + 2, self.max_lines - 4)  # Add 2 for border, limit height
        
        # Calculate popup position
        popup_y = max(0, (self.max_lines // 2) - (popup_height // 2))
        popup_x = max(0, (self.max_cols // 2) - (popup_width // 2))
        
        # Create popup window
        popup = curses.newwin(popup_height, popup_width, popup_y, popup_x)
        popup.box()
        
        # Calculate which lines to show if content doesn't fit
        start_line = 0
        display_lines = popup_height - 2  # Account for borders
        
        # Draw content
        for i in range(display_lines):
            line_idx = start_line + i
            if line_idx >= len(formatted_content):
                break
                
            line = formatted_content[line_idx]
            try:
                attr = curses.A_BOLD if (line_idx == 0 or line_idx == 2 or line_idx == 6) else curses.A_NORMAL
                popup.addstr(i + 1, 2, line, attr)
            except curses.error:
                pass
        
        # Refresh popup and get input
        popup.refresh()
        key = popup.getch()  # Wait for any key
        
        # If 'j' was pressed, jump to the commit
        if key == ord('j'):
            return True, True, f"jump:{commit_id}"
        
        return True, False, None
    
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
