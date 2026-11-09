"""gitkcli's Screen: the tuikit Screen plus the git palette and the bottom
function-key bar.

The `C_*` names are the colour roles the app draws with: the library's shared
roles re-exported under gitk names, then the git-specific ones defined in
`define_colors` (in tuikit's application role band).
"""

from __future__ import annotations

import curses

import tuikit
from tuikit import colors


class Screen(tuikit.Screen):
    # Library roles, under the names the app uses.
    C_NORMAL = colors.NORMAL  # normal text
    C_ERROR = colors.ERROR  # error text
    C_STATUS = colors.SUCCESS  # status text (green)
    C_DATA = colors.ACCENT  # dates, active borders (blue)
    C_MATCH = colors.MATCH  # search match
    C_DIM = colors.DIM  # debug text / neutral grey
    C_TITLE = colors.TITLE  # dialog title bars and header buttons
    C_BANNER = colors.BANNER  # warning title bar (white on red)

    # Git roles.
    C_GIT_ID = colors.APP_FIRST + 0  # commit ids
    C_AUTHOR = colors.APP_FIRST + 1
    C_DIFF_DEL = colors.APP_FIRST + 2  # diff -
    C_DIFF_ADD = colors.APP_FIRST + 3  # diff +
    C_DIFF_RANGE = colors.APP_FIRST + 4  # diff @@ ranges
    C_DIFF_INFO = colors.APP_FIRST + 5  # diff headers
    C_REF_LOCAL = colors.APP_FIRST + 6
    C_TAG = colors.APP_FIRST + 7  # tags (and warnings in the log)
    C_HEAD = colors.APP_FIRST + 8
    C_STASH = colors.APP_FIRST + 9
    C_REF_REMOTE = colors.APP_FIRST + 10
    # Commit-graph lane colours: C_LANE + 0 .. C_LANE + LANE_COLORS - 1.
    C_LANE = colors.APP_FIRST + 11
    LANE_COLORS = 6

    def define_colors(self):
        define = colors.define
        define(Screen.C_GIT_ID, curses.COLOR_YELLOW)
        define(Screen.C_AUTHOR, curses.COLOR_GREEN)
        define(Screen.C_DIFF_DEL, curses.COLOR_RED)
        define(Screen.C_DIFF_ADD, curses.COLOR_GREEN)
        define(Screen.C_DIFF_RANGE, curses.COLOR_CYAN)
        define(Screen.C_DIFF_INFO, curses.COLOR_BLUE)
        define(Screen.C_REF_LOCAL, curses.COLOR_GREEN)
        define(Screen.C_TAG, curses.COLOR_YELLOW)
        define(Screen.C_HEAD, curses.COLOR_BLUE)
        define(Screen.C_STASH, curses.COLOR_CYAN)
        define(Screen.C_REF_REMOTE, curses.COLOR_RED)
        for i, fg in enumerate(
            (
                curses.COLOR_GREEN,
                curses.COLOR_RED,
                curses.COLOR_BLUE,
                curses.COLOR_MAGENTA,
                curses.COLOR_YELLOW,
                curses.COLOR_CYAN,
            )
        ):
            define(Screen.C_LANE + i, fg)

    def __init__(self, app, stdscr):
        super().__init__(app, stdscr)
        # The function-key bar. Callbacks reach the views lazily through
        # self.app, so they are safe to define before those views exist. F7
        # from the keyboard is special-cased in App.handle_global_key (it opens
        # at the selected row); a click on the bar opens at the mouse.
        app = self.app
        self.status_bar.set_entries(
            [
                ("F1", "Git Log", lambda: app.git_log.show()),
                ("F2", "Git Refs", lambda: app.git_refs.show()),
                ("F3", "Git Diff", lambda: app.git_diff.show()),
                ("F4", "Logs", lambda: app.log.view.show()),
                ("F5", "Refresh", lambda: app.refresh_all()),
                ("F6", "Search", lambda: app.open_search()),
                ("F7", "Context", lambda: app.open_context_menu(at_selection=False)),
                ("F8", "Command", lambda: app.command_dialog.show()),
                ("F9", "Config", lambda: app.preferences.show()),
                ("F10", "Quit", lambda: app.exit_program()),
            ]
        )
