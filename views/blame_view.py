"""
Blame view for displaying file blame information
"""
import curses
from views.base_view import BaseView

class BlameView(BaseView):
    """View for displaying file blame information"""
    
    def __init__(self, stdscr, repository, commit_id, file_path):
        """
        Initialize blame view
        
        Args:
            stdscr: Curses window object
            repository: Repository instance
            commit_id: ID of commit to blame from
            file_path: Path to file for blame
        """
        super().__init__(stdscr)
        self.repository = repository
        self.commit_id = commit_id
        self.file_path = file_path
        self.blame_data = repository.get_blame(commit_id, file_path)
        self.blame_top = 0
        
        # Calculate column widths for display
        self.commit_width = 8  # Short commit ID
        self.author_width = 15
        self.date_width = 10
        self.linenum_width = 5
        
    def draw(self):
        """Draw the blame view"""
        if not self.blame_data:
            self.stdscr.addstr(0, 0, f"No blame data for {self.file_path}", curses.color_pair(1))
            return
            
        # Draw header
        header = f"Blame for {self.file_path}"
        self.draw_header(header)
        
        # Draw blame content
        self._draw_blame_content()
        
        # Draw status line
        status = (f" Lines {self.blame_top + 1}-{min(self.blame_top + self.max_lines - 2, len(self.blame_data))}/"
                 f"{len(self.blame_data)} | Press ENTER to return ")
        self.draw_status(status)
        
    def _draw_blame_content(self):
        """Draw the blame content"""
        # Calculate visible lines
        display_count = min(len(self.blame_data), self.max_lines - 2)
        
        # Calculate content width
        content_width = self.max_cols - (self.commit_width + self.author_width + 
                                        self.date_width + self.linenum_width + 4)
        
        # Draw blame lines
        for i in range(display_count):
            idx = self.blame_top + i
            if idx >= len(self.blame_data):
                break
                
            commit_id, author, date, line_num, content = self.blame_data[idx]
            
            # Truncate fields if needed
            if len(author) > self.author_width:
                author = author[:self.author_width - 1] + "…"
                
            if len(content) > content_width:
                content = content[:content_width - 3] + "..."
            
            # Draw line components
            y_pos = i + 1
            try:
                # Commit ID
                self.stdscr.addstr(y_pos, 0, commit_id.ljust(self.commit_width), 
                                 curses.color_pair(2))
                # Author
                self.stdscr.addstr(y_pos, self.commit_width, author.ljust(self.author_width), 
                                 curses.color_pair(3))
                # Date
                self.stdscr.addstr(y_pos, self.commit_width + self.author_width, 
                                 date.ljust(self.date_width), curses.color_pair(4))
                # Line number
                self.stdscr.addstr(y_pos, self.commit_width + self.author_width + self.date_width, 
                                 str(line_num).rjust(self.linenum_width), curses.color_pair(1))
                # Content
                self.stdscr.addstr(y_pos, self.commit_width + self.author_width + 
                                 self.date_width + self.linenum_width, 
                                 " " + content, curses.color_pair(1))
            except curses.error:
                # Ignore potential curses errors
                pass
                
    def _handle_specific_key(self, key):
        """
        Handle blame view specific keys
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        if key == ord('q') or key == 10 or key == curses.KEY_ENTER:
            return True, True, f"diff:{self.commit_id}"  # Back to diff view
            
        # Simple scrolling with no cursor
        if key == ord('j') or key == curses.KEY_DOWN:
            if self.blame_top < len(self.blame_data) - (self.max_lines - 2):
                self.blame_top += 1
        elif key == ord('k') or key == curses.KEY_UP:
            if self.blame_top > 0:
                self.blame_top -= 1
        elif key == ord('d') or key == curses.KEY_NPAGE:  # Page down
            page_size = self.max_lines - 3
            new_top = min(self.blame_top + page_size, 
                         len(self.blame_data) - (self.max_lines - 2))
            self.blame_top = max(0, new_top)
        elif key == ord('u') or key == curses.KEY_PPAGE:  # Page up
            page_size = self.max_lines - 3
            self.blame_top = max(0, self.blame_top - page_size)
        
        return True, False, None  # Continue program, no view change
