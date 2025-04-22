"""
Main application controller
"""
import curses
import time
import sys

from models import Repository
from views import CommitView, DiffView, BlameView, HelpView, LoadingView
from utils import copy_to_clipboard, setup_colors, show_message

class AppController:
    """Main application controller"""
    
    def __init__(self, stdscr):
        """
        Initialize the application controller
        
        Args:
            stdscr: Curses window object
        """
        self.stdscr = stdscr
        self.repository = Repository()
        self.running = True
        self.current_view = None
        self.previous_view = None
        self.view_history = []
        
        # Set up curses
        self._setup_curses()
        
        # Open repository
        self._open_repository()
        
    def _setup_curses(self):
        """Set up curses environment"""
        # Hide cursor
        curses.curs_set(0)
        
        # Set up colors
        setup_colors()
        
    def _open_repository(self):
        """Open Git repository"""
        # Show loading view
        loading_view = LoadingView(self.stdscr)
        loading_view.refresh()
        
        # Try to open repository
        if not self.repository.open():
            self._exit_with_message("Not in a git repository")
            
    def run(self, args=None):
        """
        Run the application
        
        Args:
            args: Command line arguments
        """
        # Show loading view while loading commits
        loading_view = LoadingView(self.stdscr)
        loading_view.refresh()
        
        # Load commits
        self.repository.load_commits(args)
        
        if not self.repository.commits:
            self._exit_with_message("No commits to display")
            
        # Set initial view
        self.current_view = CommitView(self.stdscr, self.repository)
        
        # Main loop
        while self.running:
            # Refresh the current view
            self.current_view.refresh()
            
            try:
                # Get user input
                key = self.stdscr.getch()
                
                # Handle key in current view
                continue_program, switch_view, view_name = self.current_view.handle_key(key)
                
                if not continue_program:
                    self.running = False
                    break
                    
                if switch_view:
                    self._switch_view(view_name)
            except KeyboardInterrupt:
                self.running = False
                break
                
    def _switch_view(self, view_name):
        """
        Switch to another view
        
        Args:
            view_name: Name of view to switch to or command
        """
        # Save current view for history
        if not isinstance(self.current_view, HelpView):
            self.previous_view = self.current_view
        
        # Handle special commands
        if view_name == "previous":
            # Switch back to previous view
            if self.previous_view:
                self.current_view = self.previous_view
            return
            
        elif view_name == "refresh":
            # Refresh commits
            loading_view = LoadingView(self.stdscr)
            loading_view.refresh()
            self.repository = Repository()
            self.repository.open()
            self.repository.load_commits()
            self.current_view = CommitView(self.stdscr, self.repository)
            return
            
        elif view_name and view_name.startswith("copy:"):
            # Copy to clipboard
            text = view_name[5:]
            success = copy_to_clipboard(text)
            message = "Copied to clipboard" if success else "Failed to copy to clipboard"
            show_message(self.stdscr, message)
            return
        
        # Switch to named view
        if view_name == "help":
            self.current_view = HelpView(self.stdscr)
            
        elif view_name == "commit":
            self.current_view = CommitView(self.stdscr, self.repository)
            
        elif view_name and view_name.startswith("diff:"):
            # Extract commit ID from command
            commit_id = view_name[5:]
            self.current_view = DiffView(self.stdscr, self.repository, commit_id)
            
        elif view_name and view_name.startswith("blame:"):
            # Extract commit ID and file path from command
            parts = view_name[6:].split(":", 1)
            if len(parts) == 2:
                commit_id, file_path = parts
                self.current_view = BlameView(self.stdscr, self.repository, commit_id, file_path)
                
    def _exit_with_message(self, message):
        """
        Exit with a message
        
        Args:
            message: Message to display before exiting
        """
        self.stdscr.clear()
        self.stdscr.addstr(0, 0, message)
        self.stdscr.refresh()
        time.sleep(2)
        curses.endwin()
        sys.exit(1)
