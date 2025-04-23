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
        
        # Search properties
        self.search_string = ""
        self.search_active = False
        self.search_results = []
        self.search_index = -1
        self.search_types = ["message", "path", "content"]
        self.search_type_index = 0  # Default to search by message
        
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
            search_type_display = {
                "message": "Message",
                "path": "Path",
                "content": "Content"
            }
            current_type = search_type_display[self.search_types[self.search_type_index]]
            status = f" Search [{current_type}]: {self.search_string} (Tab to change search type)"
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
            
            tagPrefix = "tag: "

            # Truncate if too long to leave space for refs
            max_line_length = self.max_cols - 1
            if commit.refs:
                # Calculate the length of all formatted refs
                formatted_refs = []
                total_refs_length = 0
                
                for ref in commit.refs:
                    # Format tags differently
                    if ref.startswith(tagPrefix):
                        formatted_ref = ref[len(tagPrefix):]
                    else:
                        formatted_ref = ref
                    
                    formatted_refs.append(formatted_ref)
                    total_refs_length += len(formatted_ref) + 3  # +3 for brackets and space
                
                max_line_length = self.max_cols - total_refs_length - 1
                
            if len(line) > max_line_length:
                line = line[:max_line_length - 3] + "..."

            selected_color = 100 if is_selected else 0

            # Draw the line
            attr = curses.color_pair(1 + selected_color)
            
            # Highlight search matches if we're searching
            if self.search_string and idx in self.search_results:
                # Use a different color for search matches
                attr = curses.color_pair(5 + selected_color) | curses.A_BOLD
            
            try:
                self.stdscr.addstr(i + 1, 0, line, attr)
                
                # Add refs with different colors if they exist
                if commit.refs:
                    pos = len(line)
                    self.stdscr.addstr(i + 1, pos, " ", attr)
                    pos += 1
                    for j, ref in enumerate(commit.refs):
                        # Determine color for different ref types
                        if ref.startswith(tagPrefix):
                            ref = ref[len(tagPrefix):]
                            ref = '<' + ref + '>'
                            ref_attr = curses.color_pair(11 + selected_color)  # Yellow for tags
                        else:
                            ref = '[' + ref + ']'
                            ref_attr = curses.color_pair(12 + selected_color)  # Green for branches
                            
                        if is_selected:
                            ref_attr |= curses.A_BOLD
                            
                        # Add the ref
                        self.stdscr.addstr(i + 1, pos, ref, ref_attr)
                        pos += len(ref)
                        
                        # Add separator if not the last ref
                        if j < len(commit.refs) - 1:
                            self.stdscr.addstr(i + 1, pos, " ", attr)
                            pos += 1
                    
                    # Fill the rest of the line
                    remaining_space = self.max_cols - 1 - pos
                    if remaining_space > 0:
                        self.stdscr.addstr(i + 1, pos, " " * remaining_space, attr)
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
        elif key == ord('h'):
            return True, True, "help"  # Show help view
        
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
        elif key == 9:  # Tab key - cycle through search types
            self.search_type_index = (self.search_type_index + 1) % len(self.search_types)
        elif key == 10 or key == curses.KEY_ENTER:  # Enter key
            # Complete search and execute
            self.search_active = False
            
            # Store current search type for debugging
            search_type = self.search_types[self.search_type_index]
            
            # Run search with a reasonable timeout
            try:
                # Set cursor to indicate we're working
                curses.curs_set(1)
                self.stdscr.addstr(self.max_lines - 1, 0, f" Searching... (type: {search_type})")
                self.stdscr.refresh()
                
                # Perform the search
                self._perform_search()
                
                # Reset cursor
                curses.curs_set(0)
                
                if self.search_results:
                    self._next_search_result()
            except Exception as e:
                # If search fails, show error and reset search state
                self.stdscr.addstr(self.max_lines - 1, 0, f" Search error: {str(e)[:40]}")
                self.stdscr.refresh()
                curses.napms(1500)  # Show error for 1.5 seconds
                
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
        
        search_term = self.search_string
        if not search_term:
            return
            
        # Get current search type
        search_type = self.search_types[self.search_type_index]
        
        # Use repository's search function
        self.search_results = self.repository.search_commits(search_term, search_type, use_regex=True)
                
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
            
        # Use the default handler for non-search mode
        return super().handle_key(key)
