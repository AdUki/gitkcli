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
        self.graph_width = 10  # Width allocated for the commit graph (future use)
        
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

    def _generate_graph_column(self, commits):
        """
        Generate graph column characters for visual commit history - optimized version
        
        Args:
            commits: List of commit objects
            
        Returns:
            dict: Mapping of commit index to graph column string
        """
        if not commits:
            return {}
        
        # Track active branches as [column_index, target_commit_id]
        active_branches = []
        
        # Result will map commit index to its graph column string
        graph_columns = {}
        
        # Process each commit
        for idx, commit in enumerate(commits):
            # Create a graph array with enough space for all active branches plus one
            max_col = 0
            if active_branches:
                max_col = max(col for col, _ in active_branches)
            graph = [''] * (max_col + 2)  # +2 for safety
            
            # Find if commit is on an existing branch
            found_branch = None
            
            # Check if this commit is a target of any active branch
            for i, (col, target_id) in enumerate(active_branches):
                if target_id == commit.id:
                    found_branch = (i, col, target_id)
                    break
            
            if found_branch:
                i, col, _ = found_branch
                
                # Make sure col is valid
                if col < len(graph):
                    graph[col] = '*'  # Mark current commit
                
                # Remove this branch - we've found the commit
                if i < len(active_branches):
                    active_branches.pop(i)
                
                # Add parents as new targets if they're in our commit list
                parent_ids = [p for p in commit.parents if any(c.id == p for c in commits[idx+1:])]
                
                if parent_ids:
                    # Continue this branch with first parent
                    active_branches.append([col, parent_ids[0]])
                    
                    # Additional parents create new branches
                    for parent_id in parent_ids[1:]:
                        # Find unused column - but limit to self.graph_width
                        new_col = 0
                        used_cols = set(br[0] for br in active_branches)
                        while new_col in used_cols and new_col < self.graph_width - 1:
                            new_col += 1
                        
                        # If we've reached the width limit, skip this branch
                        if new_col >= self.graph_width - 1:
                            continue
                        
                        # Add branch for this parent
                        active_branches.append([new_col, parent_id])
                        
                        # Ensure graph has enough space
                        while new_col >= len(graph):
                            graph.append('')
                        
                        # Mark connections for merge
                        if new_col > col:
                            for j in range(col+1, new_col):
                                if j < self.graph_width:  # Limit to graph width
                                    while j >= len(graph):
                                        graph.append('')
                                    graph[j] = '-'
                            if new_col < self.graph_width:
                                graph[new_col] = '\\'
                        elif new_col < col:
                            for j in range(new_col+1, col):
                                if j < self.graph_width:  # Limit to graph width
                                    while j >= len(graph):
                                        graph.append('')
                                    graph[j] = '-'
                            if new_col < self.graph_width:
                                graph[new_col] = '/'
            else:
                # If not found, this is a new branch
                # Find first available column
                col = 0
                used_cols = set(br[0] for br in active_branches)
                while col in used_cols and col < self.graph_width - 1:
                    col += 1
                
                # If we've reached the width limit, use the last column
                if col >= self.graph_width - 1:
                    col = self.graph_width - 1
                
                # Ensure graph has enough space
                while col >= len(graph):
                    graph.append('')
                
                # Mark current commit
                graph[col] = '*'
                
                # Add parents as targets if they're in the list
                parent_ids = [p for p in commit.parents if any(c.id == p for c in commits[idx+1:])]
                if parent_ids:
                    active_branches.append([col, parent_ids[0]])
            
            # Draw vertical lines for active branches
            for col, _ in active_branches:
                if 0 <= col < len(graph) and col < self.graph_width and not graph[col]:
                    graph[col] = '|'
            
            # Limit graph to self.graph_width
            graph_str = ''.join(graph[:self.graph_width])
            
            # Store the graph string for this commit
            graph_columns[idx] = graph_str
        
        return graph_columns

    def _draw_commits(self):
        """Draw the list of commits with graph view"""
        if not self.commits:
            return
            
        # Calculate visible commits range
        visible_begin = self.top_index
        visible_end = min(len(self.commits), self.top_index + self.max_lines - 2)
        
        if visible_begin >= visible_end:
            # Safety check - adjust top_index if needed
            visible_begin = max(0, visible_end - 1)
            self.top_index = visible_begin
        
        # Generate graph columns only for visible commits
        visible_commits = self.commits[visible_begin:visible_end]
        
        try:
            graph_columns = self._generate_graph_column(visible_commits)
        except Exception:
            # Fallback if graph generation fails
            graph_columns = {}
        
        display_count = min(len(self.commits) - self.top_index, self.max_lines - 2)
        
        for i in range(display_count):
            try:
                idx = self.top_index + i
                if idx >= len(self.commits):
                    break
                    
                commit = self.commits[idx]
                is_selected = idx == self.current_index
                
                # Format the graph column if available
                graph_col = ""
                rel_idx = i  # Relative index in visible_commits
                if rel_idx in graph_columns:
                    # Ensure graph column is exactly self.graph_width characters
                    graph_str = graph_columns[rel_idx]
                    if len(graph_str) > self.graph_width:
                        graph_str = graph_str[:self.graph_width]
                    else:
                        graph_str = graph_str.ljust(self.graph_width)
                    graph_col = graph_str + " "
                else:
                    # If no graph column, fill with spaces
                    graph_col = " " * (self.graph_width + 1)
                
                # Format the commit info with aligned columns
                commit_id = commit.short_id.ljust(self.id_width)
                date = commit.date.ljust(self.date_width)
                author = commit.author[:self.author_width].ljust(self.author_width)
                
                # Track field positions for coloring
                id_start = len(graph_col)
                id_end = id_start + len(commit_id)
                
                date_start = id_end
                date_end = date_start + len(date)
                
                author_start = date_end
                author_end = author_start + len(author)
                
                # Build the commit message
                message_start = author_end + 1
                basic_info = f"{graph_col}{commit_id}{date}{author} {commit.message}"
                
                # Format refs more like git log --decorate
                refs_str = ""
                if commit.refs:
                    refs_formatted = []
                    head_refs = []
                    branch_refs = []
                    remote_refs = []
                    tag_refs = []
                    
                    # Sort refs by type
                    for ref in commit.refs:
                        if ref.startswith("HEAD"):
                            head_refs.append(ref)
                        elif ref.startswith("tag:"):
                            tag_refs.append(ref)
                        elif "/" in ref:  # Remote branches typically have a slash
                            remote_refs.append(ref)
                        else:
                            branch_refs.append(ref)
                    
                    # Combine refs in order: HEAD, branches, remotes, tags
                    all_sorted_refs = head_refs + branch_refs + remote_refs + tag_refs
                    refs_str = f" ({', '.join(all_sorted_refs)})"
                
                # Combine the basic info with refs
                full_line = basic_info + refs_str
                
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
                    
                    # Calculate positions after horizontal scrolling
                    h_start = self.h_scroll
                    
                    # Color the commit fields
                    field_colors = [
                        (id_start, id_end, 2),         # Commit ID - Yellow
                        (date_start, date_end, 4),     # Date - Cyan
                        (author_start, author_end, 3), # Author - Green
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
                    
                    # Color the refs separately if they exist
                    if refs_str and basic_info and h_start < len(full_line):
                        refs_start = len(basic_info) - h_start if len(basic_info) > h_start else 0
                        refs_end = len(full_line) - h_start if len(full_line) > h_start else 0
                        
                        if refs_start < self.max_cols and refs_end > refs_start:
                            refs_attr = curses.color_pair(11 + selected_color)  # Use tag color for refs
                            if is_selected:
                                refs_attr |= curses.A_BOLD
                            
                            refs_visible = full_line[h_start + refs_start:h_start + refs_end]
                            self.stdscr.addstr(i + 1, refs_start, refs_visible, refs_attr)
                    
                    # Color the graph column
                    if graph_col and h_start < len(graph_col):
                        graph_vis_start = 0
                        graph_vis_end = len(graph_col) - h_start
                        
                        if graph_vis_end > 0:
                            graph_attr = curses.color_pair(13 + selected_color)
                            if is_selected:
                                graph_attr |= curses.A_BOLD
                            
                            graph_visible = full_line[h_start:h_start + graph_vis_end]
                            self.stdscr.addstr(i + 1, 0, graph_visible, graph_attr)
                    
                except curses.error:
                    pass
            except Exception:
                # Skip this commit if there's any error
                continue

    def move_selection(self, delta):
        """
        Move the selection by delta
        
        Args:
            delta: Number of positions to move
        """
        if not self.commits:
            return
            
        old_index = self.current_index
        new_index = max(0, min(len(self.commits) - 1, self.current_index + delta))
        
        # Only change if actually moving
        if new_index != old_index:
            self.current_index = new_index
        
        # Adjust top_index if needed
        visible_lines = max(1, self.max_lines - 3)  # Ensure at least 1 visible line
        
        if self.current_index < self.top_index:
            self.top_index = self.current_index
        elif self.current_index >= self.top_index + visible_lines:
            self.top_index = max(0, self.current_index - visible_lines + 1)
            
        # Safety check - ensure top_index is valid
        if self.top_index >= len(self.commits):
            self.top_index = max(0, len(self.commits) - 1)
    
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
                # Perform incremental search
                self._perform_search()
        
        # Handle printable characters
        elif 32 <= key <= 126:  # Printable ASCII
            self.search_string += chr(key)
            # Perform incremental search
            self._perform_search()
        
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
