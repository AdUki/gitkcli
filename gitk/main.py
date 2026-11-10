"""Application entry point: argv parsing, curses bootstrap, and component
wiring.

`main()` parses argv (help passthrough, --no-color, --graph), then
`launch_curses()` creates the App, builds and wires every component, seeds them
from saved config, and runs tuikit's event loop (App.main_loop) until exit.
"""

import curses
import subprocess
import sys
import typing

from tuikit.dialogs import ConfirmDialog, ErrorDialog

from gitk.app import App
from gitk.config import load_config
from gitk.dialogs import (
    CommandDialogPopup,
    PreferencesDialogPopup,
    ShortcutsDialogPopup,
)
from gitk.ids import ID_CONFIRM_DIALOG, ID_ERROR_DIALOG
from gitk.keymap import Keymap
from gitk.log import Log
from gitk.screen import Screen
from gitk.split_layout import SPLIT_SIDE, SPLIT_STACKED
from gitk.views import ContextMenu, GitDiffView, GitLogView, GitRefsView


def launch_curses(stdscr, git_args: typing.List, cmd_args: typing.List):

    app = App()

    app.screen = Screen(app, stdscr)
    app.log = Log(app)
    app.git_log = GitLogView(app, git_args, cmd_args)
    app.git_diff = GitDiffView(app)
    app.git_refs = GitRefsView(app)
    app.split.attach(app.git_log, app.git_diff)
    app.context_menu = ContextMenu(app)
    app.preferences = PreferencesDialogPopup(app)
    app.shortcuts = ShortcutsDialogPopup(app)
    app.command_dialog = CommandDialogPopup(app)
    app.confirm_dialog = ConfirmDialog(app, ID_CONFIRM_DIALOG)
    app.error_dialog = ErrorDialog(app, ID_ERROR_DIALOG)

    _cfg = load_config()
    app.git_log.show_commit_id = _cfg["git_log"]["show_commit_id"]
    app.git_log.show_commit_date = _cfg["git_log"]["show_commit_date"]
    app.git_log.show_commit_author = _cfg["git_log"]["show_commit_author"]
    app.git_log.set_pref_flags(_cfg["git_log"]["flags"])
    dot = _cfg["git_log"]["graph_dot"]
    # one narrow character, or the default: the lanes are laid out in columns
    if isinstance(dot, str) and len(dot) == 1 and dot.isprintable():
        app.git_log.graph_dot = dot
    app.git_diff.ignore_whitespace = _cfg["git_diff"]["ignore_whitespace"]
    app.log.view.autoscroll = _cfg["log"]["autoscroll"]
    app.split.default_view_mode = _cfg["view"]["default_mode"]
    app.keymap = Keymap(_cfg["keys"])

    app.log.info("Application started")

    app.git_refs.job.start_job()
    # Initial load via reload_commits so it shows the same Working bar as a
    # refresh (and probes the uncommitted pseudo-rows).
    app.git_log.reload_commits()

    if app.split.default_view_mode in (SPLIT_SIDE, SPLIT_STACKED):
        app.split.set_split_mode(app.split.default_view_mode)
    else:
        app.git_log.show()

    app.main_loop()

    app.exit_program()

    app.log.info("Application ended")


def main():
    args = sys.argv[1:]

    # Check for help flags
    if "-h" in args:
        subprocess.run(["git", "log", "-h"])
        sys.exit(0)
    if "--help" in args:
        subprocess.run(["git", "log", "--help"])
        sys.exit(0)

    git_args = []
    cmd_args = []

    for arg in args:
        if arg == "--no-color":
            # Force the monochrome tier regardless of TERM (same effect as the
            # NO_COLOR env var). Not a git arg, so it never reaches `git log`.
            Screen.force_mono = True
        elif arg == "--graph":
            git_args.append(arg)
        else:
            cmd_args.append(arg)

    curses.wrapper(lambda stdscr: launch_curses(stdscr, git_args, cmd_args))


if __name__ == "__main__":
    main()
