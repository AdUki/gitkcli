#!/usr/bin/env python3
"""
GitkCLI - Terminal-based Git repository viewer
"""
import curses
import argparse
import sys
import os

# Adjust Python path - add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Direct imports from the local modules
from controllers.app_controller import AppController

def parse_arguments():
    """
    Parse command line arguments
    
    Returns:
        list: Command line arguments for git log
    """
    parser = argparse.ArgumentParser(description="Interactive command-line gitk")
    parser.add_argument("--all", action="store_true", help="Show all branches")
    parser.add_argument("--author", help="Filter by author")
    parser.add_argument("--since", help="Show commits more recent than a specific date")
    parser.add_argument("--until", help="Show commits older than a specific date")
    parser.add_argument("--grep", help="Filter commits by message")
    parser.add_argument("-n", "--max-count", type=int, help="Limit number of commits")
    parser.add_argument("--merges", action="store_true", help="Show only merge commits")
    parser.add_argument("--no-merges", action="store_true", help="Hide merge commits")
    parser.add_argument("--first-parent", action="store_true", 
                       help="Follow only the first parent commit upon seeing a merge")
    parser.add_argument("paths", nargs="*", help="Limit commits to those affecting specific paths")
    
    # Parse arguments, handling parsing errors gracefully
    try:
        args = parser.parse_args()
    except SystemExit:
        # If argument parsing fails, use default values
        args = argparse.Namespace(
            all=True,
            author=None,
            since=None,
            until=None,
            grep=None,
            max_count=None,
            merges=False,
            no_merges=False,
            first_parent=False,
            paths=[]
        )
    
    # Convert namespace to git log arguments
    log_args = []
    if args.all:
        log_args.append("--all")
    if args.author:
        log_args.append(f"--author={args.author}")
    if args.since:
        log_args.append(f"--since={args.since}")
    if args.until:
        log_args.append(f"--until={args.until}")
    if args.grep:
        log_args.append(f"--grep={args.grep}")
    if args.max_count:
        log_args.append(f"-n{args.max_count}")
    if args.merges:
        log_args.append("--merges")
    if args.no_merges:
        log_args.append("--no-merges")
    if args.first_parent:
        log_args.append("--first-parent")
    if args.paths:
        log_args.append("--")
        log_args.extend(args.paths)
    
    return log_args

def main_curses(stdscr):
    """
    Main application function to be wrapped by curses
    
    Args:
        stdscr: Curses standard screen
    """
    # Parse arguments
    log_args = parse_arguments()
    
    try:
        # Initialize and run the application
        app = AppController(stdscr)
        app.run(log_args)
    except Exception as e:
        # Handle unexpected exceptions
        curses.endwin()
        print(f"Error: {e}", file=sys.stderr)
        return 1
        
    return 0

def main():
    """
    Entry point function that wraps the curses application
    """
    try:
        return curses.wrapper(main_curses)
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        print("Interrupted by user", file=sys.stderr)
        return 130

if __name__ == "__main__":
    sys.exit(main())
