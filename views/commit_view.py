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
        
        # Column widths for aligned display
        self.id_width = 8      # Short commit ID width
        self.date_width = 11   # Date width
        self.author_width = 15 # Author width
        
        # Search properties
        self.search_string = ""
        self.search_active = False
        self.search_results = []
        self.search_index = -1
        self.search_types = ["message", "path", "content"]
        self.search_type_index = 0  # Default to search by message
        
        # Horizontal scrolling properties
        self.h_scroll = 0
        
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
            status = f" Commit {self.current_index + 1}/{len(self.commits)} | Scroll: {self.h_scroll} | Press 'H' for help "
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
            
            # Format the commit info with aligned columns
            commit_id = commit.short_id.ljust(self.id_width)
            date = commit.date.ljust(self.date_width)
            author = commit.author[:self.author_width].ljust(self.author_width)
            
            # Track field positions for coloring
            id_start = 0
            id_end = id_start + len(commit_id)
            
            date_start = id_end
            date_end = date_start + len(date)
            
            author_start = date_end
            author_end = author_start + len(author)
            
            # Build the complete line with all fields
            message_start = author_end + 2  # +2 for " "
            basic_info = f"{commit_id}{date}{author} {commit.message}"
            
            # Calculate ref info and positions
            refs = []
            ref_positions = []  # Store (start, end, is_tag) for each ref
            
            if commit.refs:
                ref_text = " "  # Space before refs
                current_pos = len(basic_info) + len(ref_text)
                
                for ref in commit.refs:
                    if ref.startswith("tag: "):
                        ref_name = ref[5:]  # Remove "tag: " prefix
                        ref_str = f"<{ref_name}>"
                        is_tag = True
                    else:
                        ref_str = f"[{ref}]"
                        is_tag = False
                    
                    # Record the position and type
                    start_pos = current_pos
                    end_pos = start_pos + len(ref_str)
                    ref_positions.append((start_pos, end_pos, is_tag))
                    
                    # Add to refs
                    refs.append(ref_str)
                    current_pos = end_pos
                    
                    # Add space unless it's the last ref
                    if ref != commit.refs[-1]:
                        refs.append(" ")
                        current_pos += 1
            
            # Combine the basic info with refs
            full_line = basic_info
            if refs:
                full_line += " " + "".join(refs)
            
            selected_color = 100 if is_selected else 0

            # Base attribute for the line
            base_attr = curses.color_pair(1 + selected_color)
            
            # Highlight search matches if we're searching
            if self.search_string and idx in self.search_results:
                # Use a different color for search matches
                base_attr = curses.color_pair(5 + selected_color) | curses.A_BOLD
            
            try:
                # Apply horizontal scrolling
                visible_start = self.h_scroll
                visible_end = visible_start + self.max_cols - 1
                
                # Draw the base line with horizontal scrolling
                if visible_start < len(full_line):
                    visible_part = full_line[visible_start:visible_end]
                    self.stdscr.addstr(i + 1, 0, visible_part, base_attr)
                else:
                    visible_part = ""
                
                # Colorize the fields if they are in the visible area
                field_colors = [
                    (id_start, id_end, 2),         # Commit ID - Yellow
                    (date_start, date_end, 4),     # Date - Cyan
                    (author_start, author_end, 3), # Author - Green
                    (message_start, len(basic_info), 1)  # Message - Default
                ]
                
                for start_pos, end_pos, color_num in field_colors:
                    # Check if any part of this field is visible
                    if end_pos > visible_start and start_pos < visible_end:
                        # Calculate visible boundaries
                        vis_start = max(0, start_pos - visible_start)
                        vis_end = min(visible_end - visible_start, end_pos - visible_start)
                        
                        # Only proceed if there's something to draw
                        if vis_start < vis_end:
                            field_attr = curses.color_pair(color_num + selected_color)
                            
                            if is_selected:
                                field_attr |= curses.A_BOLD
                            
                            # Get the visible part of the field
                            field_part = full_line[visible_start + vis_start:visible_start + vis_end]
                            
                            # Draw the colored field
                            self.stdscr.addstr(i + 1, vis_start, field_part, field_attr)
                
                # Color the refs that are within the visible area
                for start_pos, end_pos, is_tag in ref_positions:
                    # Check if any part of this ref is visible
                    if end_pos > visible_start and start_pos < visible_end:
                        # Calculate visible boundaries
                        vis_start = max(0, start_pos - visible_start)
                        vis_end = min(visible_end - visible_start, end_pos - visible_start)
                        
                        # Only proceed if there's something to draw
                        if vis_start < vis_end:
                            # Choose color based on ref type
                            if is_tag:
                                ref_attr = curses.color_pair(11 + selected_color)  # Yellow for tags
                            else:
                                ref_attr = curses.color_pair(12 + selected_color)  # Green for branches
                                
                            if is_selected:
                                ref_attr |= curses.A_BOLD
                            
                            # Get the visible part of the ref
                            ref_part = full_line[visible_start + vis_start:visible_start + vis_end]
                            
                            # Draw the colored ref
                            self.stdscr.addstr(i + 1, vis_start, ref_part, ref_attr)
                
                # Fill the rest of the line with spaces
                if len(visible_part) < self.max_cols - 1:
                    self.stdscr.addstr(i + 1, len(visible_part), " " * (self.max_cols - 1 - len(visible_part)), base_attr)
                
            except curses.error:
                # Ignore potential curses errors
                pass
    
    def handle_key(self, key):
        """
        Handle key press with common navigation functionality
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Direct handling for search mode
        if self.search_active:
            return self._handle_search_input(key)
        
        # Handle horizontal scrolling keys directly
        if key == ord('h') or key == curses.KEY_LEFT:
            self.h_scroll = max(0, self.h_scroll - 5)
            return True, False, None
        elif key == ord('l') or key == curses.KEY_RIGHT:
            self.h_scroll += 5
            return True, False, None
            
        # Common exit and help keys
        if key == ord('q'):
            return False, False, None  # Exit program
        elif key == ord('H'):
            return True, True, "help"  # Switch to help view
            
        # Let subclass handle specific navigation
        return self._handle_specific_key(key)
    
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
            
        # Check for search key
        if key == ord('/'):
            self.search_active = True
            self.search_string = ""
            return True, False, None
            
        # Handle vertical navigation keys
        if key == ord('j') or key == curses.KEY_DOWN:
            self.move_selection(1)
            return True, False, None
        elif key == ord('k') or key == curses.KEY_UP:
            self.move_selection(-1)
            return True, False, None
        elif key == ord('d') or key == curses.KEY_NPAGE:  # Page Down
            page_size = self.max_lines - 3
            self.move_selection(page_size)
            return True, False, None
        elif key == ord('u') or key == curses.KEY_PPAGE:  # Page Up
            page_size = self.max_lines - 3
            self.move_selection(-page_size)
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
