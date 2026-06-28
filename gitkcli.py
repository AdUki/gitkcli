#!/usr/bin/python

import curses
import curses.panel
import dataclasses
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import typing

# Keyboard constants and the input state classes now live in gitk.input;
# re-exported below so this file's remaining code keeps resolving them.
from gitk.input import (KEY_SHIFT_F5, KEY_CTRL_LEFT, KEY_CTRL_RIGHT,
                        KEY_CTRL_BACKSPACE, KEY_CTRL_DEL, KEY_ENTER, KEY_RETURN,
                        KEY_TAB, ENTER_KEYS, KeyboardState, MouseState)
# Screen now lives in gitk.screen; re-exported so this file's code resolves it.
from gitk.screen import Screen
# Segments (and the ref_color_and_title helper) now live in gitk.segments.
from gitk.segments import (ref_color_and_title, Segment, FillerSegment,
                           TextSegment, RefSegment, ButtonSegment, ToggleSegment,
                           SplitButtonSegment, DynamicTextSegment,
                           HighlightToggleSegment, OnOffToggleSegment, ChoiceSegment)
# List items now live in gitk.items.
from gitk.items import (Item, SeparatorItem, RefListItem, TextListItem,
                        SpacerListItem, StatListItem, DiffListItem,
                        SegmentedListItem, ButtonRowItem, button_row,
                        WindowTopBarItem, UncommittedChangesListItem,
                        CommitListItem, ContextMenuItem, UserInputListItem,
                        ResetModeItem, PreferenceRow)
# View / ListView (+ split helper and view constants) now live in gitk.view.
from gitk.view import (View, ListView, _raise_split_sibling,
                       HORIZONTAL_OFFSET_JUMP, SPLIT_DIVIDER_COLOR)

# View/dialog/job identifiers now live in gitk.ids; re-exported for this file.
from gitk.ids import *
# Background git jobs now live in gitk.jobs.
from gitk.jobs import (Job, GitLogJob, GitRefreshHeadJob, GitDiffJob,
                       GitSearchJob, GitRefsJob)

# Config persistence, the clipboard helper, and KEY_CTRL now live in gitk.config.
# Re-exported here so not-yet-extracted code in this file keeps resolving them.
from gitk.config import (KEY_CTRL, DEFAULT_CONFIG, get_config_path,
                         load_config, save_config, copy_to_clipboard)
# Modal dialog popups now live in gitk.dialogs.
from gitk.dialogs import (ResetDialogPopup, RefPushDialogPopup,
                          _RedMessageBoxPopup, ConfirmDialogPopup,
                          ErrorDialogPopup, UserInputDialogPopup,
                          PreferencesDialogPopup, NewRefDialogPopup,
                          SearchDialogPopup, GitSearchDialogPopup)
# The concrete views + context menu now live in gitk.views.
from gitk.views import (GitLogView, GitDiffView, GitRefsView, LogView,
                        ContextMenu)
# The Log and the App struct now live in gitk.log / gitk.app.
from gitk.log import Log
from gitk.app import App

def launch_curses(stdscr, git_args:typing.List, cmd_args:typing.List):

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
    app.git_log.show_commit_id     = _cfg['git_log']['show_commit_id']
    app.git_log.show_commit_date   = _cfg['git_log']['show_commit_date']
    app.git_log.show_commit_author = _cfg['git_log']['show_commit_author']
    app.git_log.set_pref_flags(_cfg['git_log']['flags'])
    app.git_diff.ignore_whitespace = _cfg['git_diff']['ignore_whitespace']
    app.log.view.autoscroll        = _cfg['log']['autoscroll']
    app.default_view_mode          = _cfg['view']['default_mode']

    app.log.info('Application started')

    app.git_refs.job.start_job()
    app.git_log.job.start_job()
    app.git_log.check_uncommitted_changes()

    if app.default_view_mode in ('side', 'stacked'):
        app.set_split_mode(app.default_view_mode)
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
                    app.log.warning(f"Curses exception: {str(e)}\n{traceback.format_exc()}")

            active_view = app.screen.get_active_view()
            if not active_view:
                break;

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

                if event_type == 'right-click' and app.mouse.left_pressed:
                    app.mouse.left_pressed = False
                    app.mouse.process_mouse_event(active_view, 'right-release')

                if (event_type == 'left-click' or event_type == 'double-click') and app.mouse.right_pressed:
                    app.mouse.right_pressed = False
                    app.mouse.process_mouse_event(active_view, 'left-release')

                app.mouse.process_mouse_event(active_view, event_type)

            elif key == curses.KEY_RESIZE:
                app.screen._full_redraw = True
                lines, cols = app.screen.getmaxyx()
                for view in app.screen.views.values():
                    view.screen_size_changed(lines, cols)
                if app.split_mode != 'off':
                    app.apply_split_layout()

            elif active_view.handle_input(app.keyboard):
                active_view.dirty = True

            else:
                if key == ord('q') or key == curses.KEY_EXIT:
                    app.screen.hide_active_view()
                elif key == KEY_CTRL_LEFT or key == KEY_CTRL('o'):
                    app.git_log.move_in_jump_list(+1)
                elif key == KEY_CTRL_RIGHT or key == KEY_CTRL('i'):
                    app.git_log.move_in_jump_list(-1)
                elif key == ord('|'):
                    app.cycle_split_view()
                elif key == KEY_CTRL('w') and app.split_active():
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

    app.log.info('Application ended')

def main():
    args = sys.argv[1:]

    # Check for help flags
    if '-h' in args:
        subprocess.run(['git', 'log', '-h'])
        sys.exit(0)
    if '--help' in args:
        subprocess.run(['git', 'log', '--help'])
        sys.exit(0)

    git_args = []
    cmd_args = []

    for arg in args:
        if arg == '--no-color':
            # Force the monochrome tier regardless of TERM (same effect as the
            # NO_COLOR env var). Not a git arg, so it never reaches `git log`.
            Screen.force_mono = True
        elif arg == '--graph':
            git_args.append(arg)
        else:
            cmd_args.append(arg)

    curses.wrapper(lambda stdscr: launch_curses(stdscr, git_args, cmd_args))

if __name__ == "__main__":
    main()
