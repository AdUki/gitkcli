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
        self.search_string = ""
        self.search_active = False
        self.search_results = []
        self.search_index = -1
        
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
        if self.search_active:
            status = f" Search: {self.search_string}"
            self.draw_status(status)
        else:
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
            
            # Truncate if too long to leave space for refs
            max_line_length = self.max_cols - 1
            if commit.refs:
                # Reserve space for refs based on their length plus some padding
                refs_str = f" [{', '.join(commit.refs)}]"
                max_line_length = self.max_cols - len(refs_str) - 1
                
            if len(line) > max_line_length:
                line = line[:max_line_length - 3] + "..."
                
            # Draw the line
            attr = curses.color_pair(9) if is_selected else curses.color_pair(1)
            
            # Highlight search matches if we're searching
            if self.search_string and idx in self.search_results:
                # Use a different color for search matches
                attr = curses.color_pair(5) | curses.A_BOLD
                # But keep selection indicator if this is the selected commit
                if is_selected:
                    attr = curses.color_pair(9) | curses.A_BOLD
            
            try:
                self.stdscr.addstr(i + 1, 0, line, attr)
                
                # Add refs with a different color if they exist
                if commit.refs:
                    refs_attr = curses.color_pair(5)  # Magenta for refs
                    if is_selected:
                        refs_attr = curses.color_pair(9) | curses.A_BOLD
                    
                    refs_str = f" [{', '.join(commit.refs)}]"
                    self.stdscr.addstr(i + 1, len(line), refs_str, refs_attr)
                    
                    # Fill the rest of the line
                    remaining_space = self.max_cols - 1 - len(line) - len(refs_str)
                    if remaining_space > 0:
                        self.stdscr.addstr(i + 1, len(line) + len(refs_str), " " * remaining_space, attr)
                else:
                    # Fill the rest of the line if no refs
                    remaining_space = self.max_cols - 1 - len(line)
                    if remaining_space > 0:
                        self.stdscr.addstr(i + 1, len(line), " " * remaining_space, attr)
                        
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
            
        # Handle search mode
        if self.search_active:
            return self._handle_search_input(key)
            
        # Check for search key
        if key == ord('/'):
            self.search_active = True
            self.search_string = ""
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
        if key == 27:  # Escape key
            # Cancel search
            self.search_active = False
        elif key == 10:  # Enter key
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
            
        # Search in commit IDs, authors, and messages
        for i, commit in enumerate(self.commits):
            searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
            if search_term in searchable_text:
                self.search_results.append(i)
                
    def _next_search_result(self):
        """Move to next search result"""
        if not self.search_results:
            return
            
        # Find next result after current index
        next_idx = None
        for idx in self.search_results:
            if idx > self.current_index:
                next_idx = idx
                break
                
        # Wrap around if no next result
        if next_idx is None and self.search_results:
            next_idx = self.search_results[0]
            
        if next_idx is not None:
            self.current_index = next_idx
            self.search_index = self.search_results.index(next_idx)
            
            # Adjust view to show the result
            if next_idx < self.top_index or next_idx >= self.top_index + self.max_lines - 2:
                self.top_index = max(0, next_idx - (self.max_lines // 4))
                
    def _prev_search_result(self):
        """Move to previous search result"""
        if not self.search_results:
            return
            
        # Find previous result before current index
        prev_idx = None
        for idx in reversed(self.search_results):
            if idx < self.current_index:
                prev_idx = idx
                break
                
        # Wrap around if no previous result
        if prev_idx is None and self.search_results:
            prev_idx = self.search_results[-1]
            
        if prev_idx is not None:
            self.current_index = prev_idx
            self.search_index = self.search_results.index(prev_idx)
            
            # Adjust view to show the result
            if prev_idx < self.top_index or prev_idx >= self.top_index + self.max_lines - 2:
                self.top_index = max(0, prev_idx - (self.max_lines // 4))
        
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
