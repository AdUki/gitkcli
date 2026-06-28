"""Application entry point: curses bootstrap, component wiring, and the main
input loop.

`main()` parses argv (help passthrough, --no-color, --graph), then
`launch_curses()` creates the App, builds and wires every component, seeds them
from saved config, and runs the draw/input loop until exit.
"""

import curses
import curses.panel
import subprocess
import sys
import traceback
import typing

from gitk.app import App
from gitk.config import KEY_CTRL, load_config
from gitk.dialogs import PreferencesDialogPopup
from gitk.input import (
    KEY_CTRL_LEFT,
    KEY_CTRL_RIGHT,
    KEY_SHIFT_F5,
    KeyboardState,
    MouseState,
)
from gitk.jobs import Job
from gitk.log import Log
from gitk.message_box import ConfirmDialogPopup, ErrorDialogPopup
from gitk.screen import Screen
from gitk.views import ContextMenu, GitDiffView, GitLogView, GitRefsView


def launch_curses(stdscr, git_args: typing.List, cmd_args: typing.List):

    app = App()

    app.screen = Screen(app, stdscr)
    app.mouse = MouseState()
    app.mouse.app = app
    app.keyboard = KeyboardState()
    app.keyboard.app = app
    app.log = Log(app)
    app.git_log = GitLogView(app, git_args, cmd_args)
    app.git_diff = GitDiffView(app)
    app.git_refs = GitRefsView(app)
    app.context_menu = ContextMenu(app)
    app.preferences = PreferencesDialogPopup(app)
    app.confirm_dialog = ConfirmDialogPopup(app)
    app.error_dialog = ErrorDialogPopup(app)

    _cfg = load_config()
    app.git_log.show_commit_id = _cfg["git_log"]["show_commit_id"]
    app.git_log.show_commit_date = _cfg["git_log"]["show_commit_date"]
    app.git_log.show_commit_author = _cfg["git_log"]["show_commit_author"]
    app.git_log.set_pref_flags(_cfg["git_log"]["flags"])
    app.git_diff.ignore_whitespace = _cfg["git_diff"]["ignore_whitespace"]
    app.log.view.autoscroll = _cfg["log"]["autoscroll"]
    app.split.default_view_mode = _cfg["view"]["default_mode"]

    app.log.info("Application started")

    app.git_refs.job.start_job()
    app.git_log.job.start_job()
    app.git_log.check_uncommitted_changes()

    if app.split.default_view_mode in ("side", "stacked"):
        app.split.set_split_mode(app.split.default_view_mode)
    else:
        app.git_log.show()

    try:
        user_input = True

        while app.running:
            update_jobs = Job.process_all_jobs()

            # A success flash auto-expires on a timer, so keep redrawing the bottom
            # bar while one is showing even if nothing else changed.
            flash = app.screen.flash_active()

            if update_jobs or user_input or flash:
                try:
                    # Draws dirty content, then composites the panel deck and the
                    # bottom bar in one doupdate().
                    app.screen.draw_visible_views()
                except curses.error as e:
                    app.log.warning(
                        f"Curses exception: {str(e)}\n{traceback.format_exc()}"
                    )

            active_view = app.screen.get_active_view()
            if not active_view:
                break

            stdscr.timeout(5 if update_jobs or flash else 100)

            user_input = app.keyboard.read(stdscr)
            if not user_input:
                # no key pressed (or an unrecognized escape sequence)
                continue

            key = app.keyboard.key

            if key == curses.KEY_MOUSE:
                if not app.mouse.read_curses_event(stdscr):
                    continue

                event_type = app.mouse.event_type

                if event_type == "right-click" and app.mouse.left_pressed:
                    app.mouse.left_pressed = False
                    app.mouse.process_mouse_event(active_view, "right-release")

                if (
                    event_type == "left-click" or event_type == "double-click"
                ) and app.mouse.right_pressed:
                    app.mouse.right_pressed = False
                    app.mouse.process_mouse_event(active_view, "left-release")

                app.mouse.process_mouse_event(active_view, event_type)

            elif key == curses.KEY_RESIZE:
                app.screen._full_redraw = True
                lines, cols = app.screen.getmaxyx()
                for view in app.screen.views.values():
                    view.screen_size_changed(lines, cols)
                if app.split.split_mode != "off":
                    app.split.apply_split_layout()

            elif active_view.handle_input(app.keyboard):
                active_view.dirty = True

            else:
                if key == ord("q") or key == curses.KEY_EXIT:
                    app.screen.hide_active_view()
                elif key == KEY_CTRL_LEFT or key == KEY_CTRL("o"):
                    app.git_log.move_in_jump_list(+1)
                elif key == KEY_CTRL_RIGHT or key == KEY_CTRL("i"):
                    app.git_log.move_in_jump_list(-1)
                elif key == ord("|"):
                    app.split.cycle_split_view()
                elif key == KEY_CTRL("w") and app.split.split_active():
                    # toggle focus between the two split panes
                    (app.git_diff if app.git_log.is_active() else app.git_log).show()
                elif key == KEY_SHIFT_F5:
                    app.reload_refs_commits()
                elif key == curses.KEY_F7:
                    # From the keyboard, open at the selected row (no mouse cursor).
                    app.open_context_menu()
                elif key in app.screen.fkey_actions:
                    app.screen.fkey_actions[key]()

    except KeyboardInterrupt:
        pass

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
