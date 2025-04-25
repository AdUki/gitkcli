"""
Command output view for displaying results of git commands
"""
import curses
import subprocess
import threading
import time
import os
from views.base_view import BaseView

class CommandOutputView(BaseView):
    """View for displaying command output"""
    
    def __init__(self, stdscr, repository, command):
        """
        Initialize command output view
        
        Args:
            stdscr: Curses window object
            repository: Repository instance
            command: Command to execute
        """
        super().__init__(stdscr)
        self.repository = repository
        self.command = command
        self.output_lines = []
        self.loading = True
        self.error = None
        self.loading_thread = None
        self.scroll_top = 0
        self.scroll_position = 0
        
        # Start executing the command in a background thread
        self._execute_command()
        
    def _execute_command(self):
        """Execute the command in a background thread"""
        def run_command():
            try:
                # Split command into components
                cmd_parts = self.command.split()
                
                # Run the command in the git repository directory
                repo_path = self.repository.repo.path
                if repo_path.endswith('.git/'):
                    # If the path is to the .git directory, use the parent directory
                    repo_path = os.path.dirname(repo_path)
                    
                # Execute command
                result = subprocess.run(
                    cmd_parts,
                    cwd=repo_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30  # Add a timeout to prevent hanging
                )
                
                # Process output
                if result.returncode == 0:
                    output = result.stdout
                    self.output_lines = output.splitlines()
                else:
                    # If command returned error, show stderr
                    error_output = result.stderr
                    self.output_lines.extend(error_output.splitlines())

                # If no output was generated, add a message
                if not self.output_lines:
                    self.output_lines = ["Command executed successfully with no output."]
                
            except subprocess.TimeoutExpired:
                self.output_lines = ["Command timed out after 30 seconds."]
                self.error = "Timeout"
            except Exception as e:
                self.error = str(e)
            finally:
                self.loading = False
        
        # Start the thread
        self.loading_thread = threading.Thread(target=run_command)
        self.loading_thread.daemon = True
        self.loading_thread.start()
        
    def draw(self):
        """Draw the command output view"""
        # Draw header
        header = f"GITK CLI - Command Output: {self.command}"
        self.draw_header(header)
        
        if self.loading:
            self._draw_loading()
        else:
            self._draw_output()
            
        # Draw status line
        if self.loading:
            status = " Loading... "
        else:
            total_lines = len(self.output_lines)
            visible_start = self.scroll_top + 1
            visible_end = min(self.scroll_top + self.max_lines - 2, total_lines)
            status = f" Lines {visible_start}-{visible_end}/{total_lines} | Press 'q' to return "
            
        self.draw_status(status)
        
    def _draw_loading(self):
        """Draw loading animation"""
        y_pos = self.max_lines // 2
        x_pos = max(0, (self.max_cols - len("Executing command...")) // 2)
        
        spinner_chars = ['|', '/', '-', '\\']
        spinner_char = spinner_chars[int(time.time() * 5) % len(spinner_chars)]
        
        try:
            self.stdscr.addstr(y_pos - 1, x_pos, f"Executing command: {self.command}", curses.color_pair(1))
            self.stdscr.addstr(y_pos, x_pos, f"{spinner_char} Running... {spinner_char}", curses.color_pair(3) | curses.A_BOLD)
            self.stdscr.addstr(y_pos + 1, x_pos, "Press 'q' to cancel", curses.color_pair(1))
        except curses.error:
            pass
            
    def _draw_output(self):
        """Draw command output content"""
        display_count = min(len(self.output_lines) - self.scroll_top, self.max_lines - 2)
        
        for i in range(display_count):
            line_idx = self.scroll_top + i
            if line_idx >= len(self.output_lines):
                break
                
            line = self.output_lines[line_idx]
            
            # Determine line style based on content
            attr = curses.color_pair(1)  # Default style
            
            # Highlight error messages
            if self.error and ("error" in line.lower() or "failed" in line.lower()):
                attr = curses.color_pair(6)  # Error color
                
            # Highlight git commit IDs
            elif line.strip().startswith("commit ") and len(line.strip()) > 10:
                attr = curses.color_pair(2)  # Commit ID color
                
            # Highlight diff additions/deletions
            elif line.startswith("+") and not line.startswith("+++"):
                attr = curses.color_pair(7)  # Addition color
            elif line.startswith("-") and not line.startswith("---"):
                attr = curses.color_pair(6)  # Deletion color
                
            # Highlight headers
            elif line.startswith("diff --git") or line.startswith("@@"):
                attr = curses.color_pair(4)  # Header color
                
            # Try to draw the line with the determined style
            try:
                self.stdscr.addstr(i + 1, 0, line[:self.max_cols-1], attr)
            except curses.error:
                pass
                
    def handle_key(self, key):
        """
        Handle key press for command output view
        
        Args:
            key: Key code
            
        Returns:
            tuple: (continue_program, switch_view, view_name)
        """
        # Return to previous view
        if key == ord('q') or key == 27:  # 'q' or Escape
            return True, True, "commit"
            
        # Handle resize event
        elif key == curses.KEY_RESIZE:
            return True, False, None
            
        # Scrolling controls
        elif key == ord('j') or key == curses.KEY_DOWN:
            self._scroll(1)
        elif key == ord('k') or key == curses.KEY_UP:
            self._scroll(-1)
        elif key == ord('d') or key == curses.KEY_NPAGE:  # Page Down
            self._scroll(self.max_lines - 3)
        elif key == ord('u') or key == curses.KEY_PPAGE:  # Page Up
            self._scroll(-(self.max_lines - 3))
        elif key == ord('g'):  # Go to top
            self.scroll_top = 0
        elif key == ord('G'):  # Go to bottom
            max_scroll = max(0, len(self.output_lines) - (self.max_lines - 2))
            self.scroll_top = max_scroll
            
        return True, False, None
        
    def _scroll(self, delta):
        """
        Scroll output by delta lines
        
        Args:
            delta: Number of lines to scroll
        """
        if not self.output_lines:
            return
            
        max_scroll = max(0, len(self.output_lines) - (self.max_lines - 2))
        self.scroll_top = max(0, min(max_scroll, self.scroll_top + delta))
