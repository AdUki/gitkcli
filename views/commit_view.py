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
        display_count = min(len(self.commits), self.max_lines - 2)
        
        for i in range(display_count):
            idx = self.top_index + i
            if idx >= len(self.commits):
                break
                
            commit = self.commits[idx]
            is_selected = idx == self.current_index
            
            commit_id = commit.short_id.ljust(self.id_width)
            date = commit.date.ljust(self.date_width)
            author = commit.author[:self.author_width].ljust(self.author_width)
            
            id_start = 0
            id_end = id_start + len(commit_id)
            
            date_start = id_end
            date_end = date_start + len(date)
            
            author_start = date_end
            author_end = author_start + len(author)
            
            message_start = author_end + 2
            basic_info = f"{commit_id}{date}{author} {commit.message}"
            
            refs = []
            ref_positions = []
            
            if commit.refs:
                ref_text = " "
                current_pos = len(basic_info) + len(ref_text)
                
                for ref in commit.refs:
                    if ref.startswith("tag: "):
                        ref_name = ref[5:]
                        ref_str = f"<{ref_name}>"
                        is_tag = True
                    else:
                        ref_str = f"[{ref}]"
                        is_tag = False
                    
                    start_pos = current_pos
                    end_pos = start_pos + len(ref_str)
                    ref_positions.append((start_pos, end_pos, is_tag))
                    
                    refs.append(ref_str)
                    current_pos = end_pos
                    
                    if ref != commit.refs[-1]:
                        refs.append(" ")
                        current_pos += 1
            
            full_line = basic_info
            if refs:
                full_line += " " + "".join(refs)
            
            selected_color = 100 if is_selected else 0
            base_attr = curses.color_pair(1 + selected_color)
            
            if self.search_string and idx in self.search_results:
                base_attr = curses.color_pair(5 + selected_color) | curses.A_BOLD
            
            try:
                if self.h_scroll < len(full_line):
                    visible_part = full_line[self.h_scroll:]
                    self.stdscr.addstr(i + 1, 0, visible_part, base_attr)
                else:
                    self.stdscr.addstr(i + 1, 0, "", base_attr)
                
                # Fill selected line with spaces to end of screen
                if is_selected:
                    visible_length = len(visible_part) if self.h_scroll < len(full_line) else 0
                    if visible_length < self.max_cols:
                        self.stdscr.addstr(i + 1, visible_length, " " * (self.max_cols - visible_length), base_attr)
                
                h_start = self.h_scroll
                
                field_colors = [
                    (id_start, id_end, 2),
                    (date_start, date_end, 4),
                    (author_start, author_end, 3),
                    (message_start, len(basic_info), 1)
                ]
                
                for start_pos, end_pos, color_num in field_colors:
                    if end_pos > h_start and start_pos < h_start + self.max_cols:
                        vis_start = max(0, start_pos - h_start)
                        vis_end = min(self.max_cols, end_pos - h_start)
                        
                        if vis_start < vis_end:
                            field_attr = curses.color_pair(color_num + selected_color)
                            
                            if is_selected:
                                field_attr |= curses.A_BOLD
                            
                            field_part = full_line[h_start + vis_start:h_start + vis_end]
                            self.stdscr.addstr(i + 1, vis_start, field_part, field_attr)
                
                for start_pos, end_pos, is_tag in ref_positions:
                    if end_pos > h_start and start_pos < h_start + self.max_cols:
                        vis_start = max(0, start_pos - h_start)
                        vis_end = min(self.max_cols, end_pos - h_start)
                        
                        if vis_start < vis_end:
                            if is_tag:
                                ref_attr = curses.color_pair(11 + selected_color)
                            else:
                                ref_attr = curses.color_pair(12 + selected_color)
                                
                            if is_selected:
                                ref_attr |= curses.A_BOLD
                            
                            ref_part = full_line[h_start + vis_start:h_start + vis_end]
                            self.stdscr.addstr(i + 1, vis_start, ref_part, ref_attr)
                
            except curses.error:
                pass
    
    def handle_key(self, key):
        """
        Handle key press for commit view
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Handle search mode separately
        if self.search_active:
            return self._handle_search_input(key)
        
        # Exit application
        if key == ord('q'):
            return False, False, None
        
        # Help view
        elif key == ord('H'):
            return True, True, "help"
        
        # Handle resize event
        elif key == curses.KEY_RESIZE:
            return True, False, None
        
        # Handle horizontal scrolling
        elif key == ord('h') or key == curses.KEY_LEFT:
            self.h_scroll = max(0, self.h_scroll - 5)
        elif key == ord('l') or key == curses.KEY_RIGHT:
            self.h_scroll += 5
        
        # Handle vertical navigation
        elif key == ord('j') or key == curses.KEY_DOWN:
            self.move_selection(1)
        elif key == ord('k') or key == curses.KEY_UP:
            self.move_selection(-1)
        elif key == ord('d') or key == curses.KEY_NPAGE:  # Page Down
            page_size = self.max_lines - 3
            self.move_selection(page_size)
        elif key == ord('u') or key == curses.KEY_PPAGE:  # Page Up
            page_size = self.max_lines - 3
            self.move_selection(-page_size)
        elif key == ord('g'):  # Go to top
            self.current_index = 0
            self.top_index = 0
        elif key == ord('G'):  # Go to bottom
            self.current_index = len(self.commits) - 1
            self.top_index = max(0, self.current_index - (self.max_lines - 3))
        
        # Search functions
        elif key == ord('/'):  # Start search
            self.search_active = True
            self.search_string = ""
        elif key == ord('n'):  # Next search result
            self._next_search_result()
        elif key == ord('N'):  # Previous search result
            self._prev_search_result()
        
        # Action commands
        elif key == 10:  # Enter key - show diff
            if self.commits:
                return True, True, f"diff:{self.commits[self.current_index].id}"
        elif key == ord('c'):  # Copy commit ID
            if self.commits:
                commit_id = self.commits[self.current_index].id
                return True, True, f"copy:{commit_id}"
        elif key == ord('r'):  # Refresh
            return True, True, "refresh"
        
        return True, False, None  # Continue program, no view change

    def _handle_search_input(self, key):
        """
        Handle key input while in search mode
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Exit application
        if key == ord('q'):
            return False, False, None
        
        # Cancel search
        elif key == 27:  # Escape key
            self.search_active = False
        
        # Cycle through search types
        elif key == 9:  # Tab key
            self.search_type_index = (self.search_type_index + 1) % len(self.search_types)
        
        # Complete search
        elif key == 10 or key == curses.KEY_ENTER:
            self.search_active = False
            search_type = self.search_types[self.search_type_index]
            
            try:
                # Show cursor while searching
                curses.curs_set(1)
                self.stdscr.addstr(self.max_lines - 1, 0, f" Searching... (type: {search_type})")
                self.stdscr.refresh()
                
                # Perform the search
                self._perform_search()
                
                # Hide cursor when done
                curses.curs_set(0)
                
                if self.search_results:
                    self._next_search_result()
            except Exception as e:
                # Show error on failure
                self.stdscr.addstr(self.max_lines - 1, 0, f" Search error: {str(e)[:40]}")
                self.stdscr.refresh()
                curses.napms(1500)
        
        # Handle text editing
        elif key == 127 or key == curses.KEY_BACKSPACE or key == curses.KEY_DC:
            if self.search_string:
                self.search_string = self.search_string[:-1]
        
        # Handle printable characters
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
