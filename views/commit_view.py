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
        self.graph_width = 10  # Width allocated for the commit graph
        
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

        # Command input properties
        self.command_active = False
        self.command_string = ""
        
        # Horizontal scrolling properties
        self.h_scroll = 0
        
        # Visible window calculation
        self.visible_commits_range = (0, 0)
        
    def draw(self):
        """Draw the commit list view"""
        # Draw header
        self.draw_header("GITK CLI - Commit History")
        
        # Always update commits reference to catch newly loaded commits
        self.commits = self.repository.commits
        
        # Check if we're still loading commits
        loading, loaded_count, total_count, error = self.repository.get_loading_status()
        
        if error:
            # Show any loading errors in a small notice, but continue showing commits
            try:
                self.stdscr.addstr(1, 0, f"Error: {error}", curses.color_pair(6))
            except curses.error:
                pass
        
        # Check if we need to show loading screen or commit list
        if not self.commits and loading:
            # Show full screen loading message
            self._draw_loading_screen(loaded_count, total_count)
        elif self.commits:
            # Draw commit list if there are commits
            self._draw_commits()
        else:
            # No commits and not loading - repository must be empty
            try:
                self.stdscr.addstr(self.max_lines // 2, 
                                  max(0, (self.max_cols - len("No commits found in this repository.")) // 2),
                                  "No commits found in this repository.", curses.color_pair(1))
            except curses.error:
                pass
            
        # Draw status line
        if self.search_active:
            search_type_display = {
                "message": "Message",
                "path": "Path",
                "content": "Content"
            }
            current_type = search_type_display[self.search_types[self.search_type_index]]
            status = f" Search [{current_type}]: {self.search_string} (Tab to change type, Esc to cancel) "
            self.draw_status(status)
        elif self.command_active:
            status = f" :{self.command_string} (Format 'git <cmd> [git-id]', Enter to execute, Esc to cancel) "
            self.draw_status(status)
        else:
            # Include loading status in the status bar
            if loading:
                if total_count > 0:
                    # Show percentage if we have a total count
                    percent = int(loaded_count/total_count*100) if total_count > 0 else 0
                    status = f" Loading: {loaded_count}/{total_count} commits ({percent}%) | Press '/' to search | ':' for commands | 'H' for help "
                else:
                    # Otherwise just show count
                    status = f" Loading: {loaded_count} commits | Press '/' to search | ':' for commands | 'H' for help "

            else:
                # Show total when loading is complete
                if self.commits:
                    status = f" {len(self.commits)} commits loaded | Commit {self.current_index + 1}/{len(self.commits)} | Press '/' to search | 'H' for help "
                else:
                    status = f" No commits found | Press 'r' to refresh or 'q' to quit "
            
            self.draw_status(status)
    
    def _draw_loading_screen(self, loaded_count, total_count):
        """Draw a loading screen with progress information"""
        try:
            # Create loading message with progress info
            if total_count > 0:
                percent = int(loaded_count/total_count*100)
                loading_text = f"Loading commits: {loaded_count}/{total_count} ({percent}%)"
            else:
                loading_text = f"Loading commits: {loaded_count}"
                
            # Add a message about large repos
            info_text = "Loading first batch of commits... Please wait."
            hint_text = "For large repositories, this may take a moment."
            
            # Calculate positions for centered text
            y_center = self.max_lines // 2
            x_loading = max(0, (self.max_cols - len(loading_text)) // 2)
            x_info = max(0, (self.max_cols - len(info_text)) // 2)
            x_hint = max(0, (self.max_cols - len(hint_text)) // 2)
            
            # Draw the loading messages
            self.stdscr.addstr(y_center - 1, x_loading, loading_text, curses.color_pair(3) | curses.A_BOLD)
            self.stdscr.addstr(y_center + 1, x_info, info_text, curses.color_pair(1))
            self.stdscr.addstr(y_center + 2, x_hint, hint_text, curses.color_pair(4))
            
            # Draw a simple progress bar
            if total_count > 0:
                bar_width = min(self.max_cols - 10, 50)  # Maximum width of 50 chars
                progress_width = int(bar_width * (loaded_count / total_count))
                
                # Draw the bar outline
                bar_x = (self.max_cols - bar_width) // 2
                self.stdscr.addstr(y_center, bar_x - 1, "[", curses.color_pair(1))
                self.stdscr.addstr(y_center, bar_x + bar_width, "]", curses.color_pair(1))
                
                # Draw the progress portion
                for i in range(progress_width):
                    self.stdscr.addstr(y_center, bar_x + i, "=", curses.color_pair(2))
                    
                # Fill the rest with spaces
                for i in range(progress_width, bar_width):
                    self.stdscr.addstr(y_center, bar_x + i, " ", curses.color_pair(1))
        except curses.error:
            # Ignore errors from drawing outside window bounds
            pass

    def _draw_commits(self):
        """Draw the list of commits with graph view"""
        if not self.commits:
            return
            
        # Ensure current_index and top_index are in bounds
        self.current_index = min(self.current_index, len(self.commits) - 1)
        self.top_index = min(self.top_index, len(self.commits) - 1)
            
        # Calculate visible commits range
        visible_begin = self.top_index
        visible_end = min(len(self.commits), self.top_index + self.max_lines - 2)
        
        # Store visible range for searching
        self.visible_commits_range = (visible_begin, visible_end)
        
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
                
                # Check if this commit is in search results
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
            
        # Handle command input mode separately
        if self.command_active:
            return self._handle_command_input(key)
        
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
        
        # Handle vertical navigation - only if we have commits
        elif (key == ord('j') or key == curses.KEY_DOWN) and self.commits:
            self.move_selection(1)
        elif (key == ord('k') or key == curses.KEY_UP) and self.commits:
            self.move_selection(-1)
        elif (key == ord('d') or key == curses.KEY_NPAGE) and self.commits:  # Page Down
            page_size = self.max_lines - 3
            self.move_selection(page_size)
        elif (key == ord('u') or key == curses.KEY_PPAGE) and self.commits:  # Page Up
            page_size = self.max_lines - 3
            self.move_selection(-page_size)
        elif key == ord('g') and self.commits:  # Go to top
            self.current_index = 0
            self.top_index = 0
        elif key == ord('G') and self.commits:  # Go to bottom
            self.current_index = len(self.commits) - 1
            self.top_index = max(0, self.current_index - (self.max_lines - 3))
        
        # Search functions
        elif key == ord('/'):  # Start search
            self.search_active = True
            self.search_string = ""
            self.search_results = []
        elif key == ord('n') and self.commits and self.search_results:  # Next search result
            self._next_search_result()
        elif key == ord('N') and self.commits and self.search_results:  # Previous search result
            self._prev_search_result()
            
        # Command input mode
        elif key == ord(':'):  # Colon key - start command input
            self.command_active = True
            self.command_string = ""
        
        # Action commands
        elif key == 10 and self.commits and self.current_index < len(self.commits):  # Enter key - show diff
            return True, True, f"diff:{self.commits[self.current_index].id}"
        elif key == ord('c') and self.commits and self.current_index < len(self.commits):  # Copy commit ID
            commit_id = self.commits[self.current_index].id
            return True, True, f"copy:{commit_id}"
        elif key == ord('r'):  # Refresh
            return True, True, "refresh"
        
        return True, False, None  # Continue program, no view change

    def _handle_command_input(self, key):
        """
        Handle key input while in command mode
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Exit command mode (not the program)
        if key == 27:  # Escape key
            self.command_active = False
            return True, False, None
        
        # Execute command
        elif key == 10 or key == curses.KEY_ENTER:  # Enter key
            self.command_active = False
            
            # Skip if command is empty
            if not self.command_string.strip():
                return True, False, None
                
            # Get current commit ID if available
            commit_id = None
            if self.commits and self.current_index < len(self.commits):
                commit_id = self.commits[self.current_index].id
                
            # Create the command to execute (add commit ID at the end)
            command_to_execute = self.command_string
            if commit_id:
                command_to_execute = f"git {command_to_execute} {commit_id}"
                
            # Execute command
            return True, True, f"execute:{command_to_execute}"
        
        # Handle text editing
        elif key == 127 or key == curses.KEY_BACKSPACE or key == curses.KEY_DC:  # Backspace/Delete
            if self.command_string:
                self.command_string = self.command_string[:-1]
        
        # Handle printable characters
        elif 32 <= key <= 126:  # Printable ASCII
            self.command_string += chr(key)
        
        return True, False, None   

    def _handle_search_input(self, key):
        """
        Handle key input while in search mode
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Exit search mode (not the program)
        if key == 27:  # Escape key
            self.search_active = False
            return True, False, None
        
        # Cycle through search types
        elif key == 9:  # Tab key
            self.search_type_index = (self.search_type_index + 1) % len(self.search_types)
            # Update the display but don't rerun search
            return True, False, None
        
        # Complete search and move to first result
        elif key == 10 or key == curses.KEY_ENTER:  # Enter key
            self.search_active = False
            if self.search_results:
                self._next_search_result()
            return True, False, None
        
        # Handle text editing
        elif key == 127 or key == curses.KEY_BACKSPACE or key == curses.KEY_DC:  # Backspace
            if self.search_string:
                self.search_string = self.search_string[:-1]
                # Perform incremental search
                self._perform_search()
        
        # Handle printable characters
        elif 32 <= key <= 126:  # Printable ASCII
            # Don't treat 'q' as quit in search mode
            self.search_string += chr(key)
            # Perform incremental search
            self._perform_search()
        
        return True, False, None
            
    def _perform_search(self):
        """
        Perform incremental search on visible commits for initial results,
        but allow pressing 'n' to search beyond visible area
        """
        # Clear previous results
        self.search_results = []
        self.search_index = -1
        
        search_term = self.search_string.lower()
        if not search_term:
            return
            
        # Get current search type
        search_type = self.search_types[self.search_type_index]
        
        # Only search in visible commits initially for better performance
        start_idx, end_idx = self.visible_commits_range
        
        # For large repositories, initially search only visible commits
        # This makes search much more responsive
        for i in range(start_idx, end_idx):
            if i >= len(self.commits):
                break
                
            commit = self.commits[i]
            
            # Simple case-insensitive search for all text content
            if search_type == "message":
                # Search in ID, author, message
                searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
                if search_term in searchable_text:
                    self.search_results.append(i)
                    
            # For more complex searches, simple string matching for performance
            elif search_type == "path" or search_type == "content":
                # Just use simple string matching on the message
                if search_term in commit.message.lower():
                    self.search_results.append(i)
    
    def _next_search_result(self):
        """
        Move to next search result, searching non-visible commits if needed
        """
        search_term = self.search_string.lower()
        if not search_term or not self.commits:
            return
            
        # Find next result after current index
        next_idx = None
        for idx in self.search_results:
            if idx > self.current_index:
                next_idx = idx
                break
                
        # If no result found below current position in visible area, 
        # search non-visible commits below until one is found
        if next_idx is None:
            # Start from current position
            start_idx = self.current_index + 1
            
            # Look through all commits below current position
            for i in range(start_idx, len(self.commits)):
                commit = self.commits[i]
                search_type = self.search_types[self.search_type_index]
                
                # Simple case-insensitive search
                if search_type == "message":
                    searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
                    if search_term in searchable_text:
                        next_idx = i
                        # Add to search results so we can cycle through them
                        if i not in self.search_results:
                            self.search_results.append(i)
                        break
                else:
                    # Simple string matching for other search types
                    if search_term in commit.message.lower():
                        next_idx = i
                        if i not in self.search_results:
                            self.search_results.append(i)
                        break
            
            # If still not found, wrap around to beginning
            if next_idx is None and self.search_results:
                next_idx = self.search_results[0]
                
        # If found a result, move to it
        if next_idx is not None:
            self.current_index = next_idx
            if next_idx in self.search_results:
                self.search_index = self.search_results.index(next_idx)
            
            # Adjust view to show the result
            if next_idx < self.top_index or next_idx >= self.top_index + self.max_lines - 2:
                self.top_index = max(0, next_idx - (self.max_lines // 4))
                # Important: Update search results for the new visible range
                self._update_visible_search_results()
                
    def _prev_search_result(self):
        """
        Move to previous search result, searching non-visible commits if needed
        """
        search_term = self.search_string.lower()
        if not search_term or not self.commits:
            return
            
        # Find previous result before current index
        prev_idx = None
        for idx in reversed(self.search_results):
            if idx < self.current_index:
                prev_idx = idx
                break
                
        # If no result found above current position in visible area, 
        # search non-visible commits above until one is found
        if prev_idx is None:
            # Start from current position
            start_idx = self.current_index - 1
            
            # Look through all commits above current position
            for i in range(start_idx, -1, -1):
                commit = self.commits[i]
                search_type = self.search_types[self.search_type_index]
                
                # Simple case-insensitive search
                if search_type == "message":
                    searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
                    if search_term in searchable_text:
                        prev_idx = i
                        # Add to search results so we can cycle through them
                        if i not in self.search_results:
                            self.search_results.append(i)
                        break
                else:
                    # Simple string matching for other search types
                    if search_term in commit.message.lower():
                        prev_idx = i
                        if i not in self.search_results:
                            self.search_results.append(i)
                        break
            
            # If still not found, wrap around to end
            if prev_idx is None and self.search_results:
                prev_idx = self.search_results[-1]
                
        # If found a result, move to it
        if prev_idx is not None:
            self.current_index = prev_idx
            if prev_idx in self.search_results:
                self.search_index = self.search_results.index(prev_idx)
            
            # Adjust view to show the result
            if prev_idx < self.top_index or prev_idx >= self.top_index + self.max_lines - 2:
                self.top_index = max(0, prev_idx - (self.max_lines // 4))
                # Important: Update search results for the new visible range
                self._update_visible_search_results()
                
    def _update_visible_search_results(self):
        """
        Update search results for the current visible range
        This ensures that matches are highlighted when scrolling/jumping
        """
        if not self.search_string:
            return
            
        search_term = self.search_string.lower()
        
        # Calculate visible range
        start_idx, end_idx = self.visible_commits_range = (
            self.top_index, 
            min(len(self.commits), self.top_index + self.max_lines - 2)
        )
        
        # Search through visible commits and add new matches
        for i in range(start_idx, end_idx):
            if i >= len(self.commits):
                break
                
            # Skip if already in search results
            if i in self.search_results:
                continue
                
            commit = self.commits[i]
            search_type = self.search_types[self.search_type_index]
            
            if search_type == "message":
                searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
                if search_term in searchable_text:
                    self.search_results.append(i)
            else:
                if search_term in commit.message.lower():
                    self.search_results.append(i)
